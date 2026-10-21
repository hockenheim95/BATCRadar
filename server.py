"""BATCRadar: FR24-style UI, BeyondATC traffic, SimConnect ownship fallback."""

from __future__ import annotations

import gzip
import json
import logging
import os
import socket
import sys
import threading
import time
import traceback
from pathlib import Path

from flask import Flask, Response, request, send_from_directory
from waitress import serve

from airport_info import build as build_airport
from fids import Fids
from airports import lookup as airport_lookup
from airports import names as airport_names
from airports import nearest as nearest_airport
from airports import warmup as warmup_airports
from actypes import warmup as warmup_actypes
from airlines import warmup as warmup_airlines
from batc_bridge import BatcBridge
from navdata import warmup as warmup_navdata
from parking import attach_gate
from parking import warmup as warmup_parking
from planes import (
    apply_sim_latlon,
    attach_flight_iata,
    clear_flightplan_path_cache,
    ensure_flightplan_paths,
    plane_from_batc,
    normalize_ownship_gs,
    plane_from_sim,
    snap_origin_to_field,
    tz_fallback,
)
from pilot_monitoring import PilotMonitor
import settings as app_settings
import msfs_autostart
import single_instance
from simconnect_bridge import SimConnectBridge
from tiles import logo_png, start_tile_workers, tile_png
from track_store import TrackStore
import trail_recon
from weather import current as weather_current
from weather import enroute as weather_enroute
from weather import metar_raw as weather_metar
from paths import bundle

PORT = app_settings.listen_port(env_port=os.environ.get("EFB_PORT"))
WEB_DIR = bundle() / "web"
VERSION_FILE = bundle() / "VERSION"


def current_version() -> str:
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def lan_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(1.5)
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()

_bridge = SimConnectBridge()
try:
    from airports import set_msfs_name_provider

    set_msfs_name_provider(_bridge.airport_name)
except Exception:
    pass
_batc = BatcBridge()
_tracks = TrackStore()
_fids = Fids()
_pm = PilotMonitor()

_lock = threading.Lock()
_state = {
    "mode": "offline",
    "ownshipSource": "none",
    "trafficSource": "none",
    "batcSession": 0,
    "simError": "",
    "ownship": None,
    "aircraft": [],
    "fids": {},
}


_HEAVY_KEYS = ("sidPath", "starPath")
_wire: dict[str, bytes] = {}
_paths: dict[str, dict] = {}
BATC_REPLY_S = 1.0
BATC_STALE_S = 12.0
LIVE_MIN_CYCLE_S = 0.15
OFFLINE_SLEEP_S = 0.25
_batc_session = 0
_batc_was_ok = False
_traffic_seq = 0


def _slim(plane: dict | None) -> dict | None:
    if plane is None:
        return None
    return {k: v for k, v in plane.items() if k not in _HEAVY_KEYS}


def _publish_wire() -> None:
    """Serialise the traffic payload once per sim tick (called with _lock held).

    SID/STAR paths are large and only needed for the selected aircraft, so they are
    served through /api/track instead of every /api/traffic response.
    """
    global _paths
    wire = dict(_state)
    wire["ownship"] = _slim(_state["ownship"])
    wire["aircraft"] = [_slim(p) for p in _state["aircraft"]]
    raw = json.dumps(wire).encode("utf-8")
    _wire["raw"] = raw
    _wire["gzip"] = gzip.compress(raw, 3)
    fleet = list(_state.get("aircraft") or [])
    own = _state.get("ownship")
    if own:
        fleet.append(own)
    _paths = {
        p["id"]: {"sidPath": p.get("sidPath") or [], "starPath": p.get("starPath") or []}
        for p in fleet
        if p.get("id") and (p.get("sidPath") or p.get("starPath"))
    }


_publish_wire()


def airport_payload(icao: str, aircraft: list[dict] | None = None, wx_pack: dict | None = None) -> dict:
    code = (icao or "").strip().upper()
    if len(code) != 4:
        return {}
    wx_pack = wx_pack or {}
    ap = airport_lookup(code)
    batc_wx = _batc.weather(code)
    wx_pending = bool(batc_wx.get("pending"))
    iata, city, airport_name = airport_names(code)
    have_metar = bool(batc_wx.get("metar") or wx_pack.get("metar"))
    payload = build_airport(
        icao=code,
        iata=iata or (ap[1] if ap else ""),
        name=airport_name or city or (ap[2] if ap else ""),
        city=city,
        airport_name=airport_name,
        batc_metar=batc_wx.get("metar") or "",
        batc_atis=batc_wx.get("atis") or "",
        sim_metar=wx_pack.get("metar") or "",
        live_metar="" if wx_pending or have_metar else weather_metar(code),
        wx=wx_pack.get("wx") or {},
        atc_runway=wx_pack.get("atcRunway") or "",
        aircraft=aircraft or [],
        wx_pending=wx_pending,
    )
    payload["tz"] = tz_fallback(code, payload.get("iata") or "", ap[3] if ap else None, ap[4] if ap else None)
    return payload


def sim_loop() -> None:
    while True:
        try:
            _sim_tick()
        except Exception as exc:
            print(f"  sim_loop error: {exc!r}", flush=True)
            traceback.print_exc()
            time.sleep(0.5)


def _sim_tick() -> None:
    global _batc_session, _batc_was_ok, _traffic_seq
    t0 = time.monotonic()
    if _batc.connected:
        since = _batc.generation()
        _batc.request_aircraft_refresh()
        _batc.wait_update(since, BATC_REPLY_S)
        if _batc.kick_if_stale(BATC_STALE_S):
            print("  BATC feed stale — reconnecting websocket", flush=True)

    sim_own, sim_ok, sim_error = _bridge.snapshot()
    batc_ok, batc_rows, batc_error = _batc.snapshot()

    others: list[dict] = []
    player_raw = None
    if batc_ok:
        for raw in batc_rows:
            if raw.get("isPlayer"):
                player_raw = raw
                continue
            try:
                others.append(plane_from_batc(raw, paths=False))
            except Exception as exc:
                print(f"  BATC plane skip: {exc!r}", flush=True)
        traffic_source = "batc"
    else:
        traffic_source = "none"

    ownship = None
    if player_raw and player_raw.get("lat") is not None:
        try:
            ownship = plane_from_batc(player_raw, paths=True)
            ownship["id"] = "ownship"
            ownship["isOwnship"] = True
            # Map marker + trail: denser SimConnect lat/lon; hdg/alt/gs/meta stay BATC.
            if sim_ok and sim_own:
                apply_sim_latlon(ownship, sim_own)
        except Exception as exc:
            print(f"  BATC ownship skip: {exc!r}", flush=True)
            ownship = None
    elif sim_ok and sim_own:
        ownship = plane_from_sim(sim_own, True)

    if ownship:
        snap_origin_to_field(ownship)
        if not ownship.get("origin"):
            near = nearest_airport(ownship.get("lat") or 0, ownship.get("lon") or 0)
            if near:
                icao, iata, name, *_rest = near
                ownship["origin"] = icao
                ownship["originIata"] = iata
                ownship["originName"] = name
        attach_gate(ownship)
        normalize_ownship_gs(ownship)
        attach_flight_iata(ownship)
        ownship["pm"] = _pm.update(ownship)
    else:
        _pm.reset()
    for plane in others:
        attach_flight_iata(plane)

    apt_icao = ""
    if ownship:
        st = (ownship.get("state") or "").lower()
        on_field = bool(ownship.get("onGround") or ownship.get("atDestination"))
        if not on_field:
            on_field = st in ("parked", "taxi", "pushback") or (
                float(ownship.get("gs") or 0) < 40 and float(ownship.get("alt") or 0) < 2500
            )
        if ownship.get("atDestination") and ownship.get("dest"):
            apt_icao = (ownship.get("dest") or "").upper()
        elif on_field:
            apt_icao = (ownship.get("origin") or ownship.get("dest") or "").upper()
        else:
            apt_icao = (ownship.get("dest") or ownship.get("origin") or "").upper()
    _bridge.set_metar_icao(apt_icao)
    fleet = ([ownship] if ownship else []) + others
    has_batc_traffic = any(ac.get("source") == "batc" for ac in fleet)

    mode = "live" if ownship or batc_ok else "offline"
    aircraft = fleet
    batc_session = _batc.session() if batc_ok else 0
    keep_tracks = set()
    if ownship and ownship.get("source") != "batc" and ownship.get("id"):
        keep_tracks.add(ownship["id"])
    if batc_ok:
        if _batc_session and batc_session != _batc_session:
            _tracks.clear(keep_tracks)
            trail_recon.invalidate()
            clear_flightplan_path_cache()
        _batc_session = batc_session
        _batc_was_ok = True
    elif _batc_was_ok:
        _tracks.clear(keep_tracks)
        trail_recon.invalidate()
        clear_flightplan_path_cache()
        _batc_was_ok = False
    if mode == "live":
        _tracks.touch_session()
        for plane in aircraft:
            if _tracks.note_leg(plane["id"], plane.get("origin"), plane.get("dest")):
                trail_recon.invalidate(plane["id"])
            _tracks.add(
                plane["id"],
                plane["lat"],
                plane["lon"],
                bool(plane.get("onGround")),
                float(plane.get("alt") or 0),
                heading=float(plane.get("hdg") or 0),
                gs=float(plane.get("gs") or 0),
                dense=bool(plane.get("isOwnship")),
            )
            start_icao = _tracks.start_icao(plane["id"])
            if start_icao:
                plane["trackStartIcao"] = start_icao
            field_alt = _tracks.field_alt(plane["id"])
            if field_alt is not None:
                plane["trackFieldAlt"] = field_alt
            flags = _tracks.traffic_flags(plane["id"])
            plane.update(flags)
            plane["trackAirborne"] = bool(flags.get("trackReady")) and not bool(plane.get("onGround"))
        _tracks.keep_only({plane["id"] for plane in aircraft})
    else:
        _tracks.maybe_reset()
    try:
        if has_batc_traffic:
            fids = _fids.update(fleet, ownship)
        else:
            _fids.clear_boards()
            fids = {}
    except Exception as exc:  # never let a board bug stop the sim loop
        print(f"  fids error: {exc!r}", flush=True)
        fids = {}
    with _lock:
        _state["mode"] = mode
        _state["ownshipSource"] = ownship["source"] if ownship else "none"
        _state["trafficSource"] = traffic_source
        _state["batcSession"] = batc_session
        _state["simError"] = batc_error or sim_error
        _state["ownship"] = ownship
        _state["aircraft"] = aircraft
        _state["version"] = current_version()
        _state["fids"] = fids
        _traffic_seq += 1
        _state["trafficSeq"] = _traffic_seq
        _state["trafficAt"] = int(time.time() * 1000)
        _state["batcFeedAgeS"] = _batc.aircraft_feed_age_s()
        _publish_wire()
    leftover = (LIVE_MIN_CYCLE_S if batc_ok else OFFLINE_SLEEP_S) - (time.monotonic() - t0)
    if leftover > 0:
        time.sleep(leftover)


app = Flask(__name__, static_folder=None)


def _wants_gzip() -> bool:
    return "gzip" in (request.headers.get("Accept-Encoding") or "").lower()


def _bytes(payload: bytes, content_type: str, gz: bytes | None = None) -> Response:
    if _wants_gzip() and (gz is not None or (len(payload) > 1024 and not content_type.startswith("image/"))):
        payload = gz if gz is not None else gzip.compress(payload, 5)
        resp = Response(payload, content_type=content_type)
        resp.headers["Content-Encoding"] = "gzip"
        resp.headers["Vary"] = "Accept-Encoding"
        return resp
    return Response(payload, content_type=content_type)


def _json_bytes(data) -> Response:
    return _bytes(json.dumps(data).encode("utf-8"), "application/json; charset=utf-8")


@app.get("/")
@app.get("/index.html")
def index_page():
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8").replace("{{VERSION}}", current_version())
    return _bytes(html.encode("utf-8"), "text/html; charset=utf-8")


@app.get("/api/version")
def api_version():
    return _json_bytes({"version": current_version()})


def _port_available(port: int) -> bool:
    if port == PORT:
        return True
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _reexec_soon() -> None:
    def go() -> None:
        time.sleep(0.45)
        if getattr(sys, "frozen", False):
            os.execv(sys.executable, [sys.executable, *sys.argv[1:]])
        else:
            os.execv(sys.executable, [sys.executable, *sys.argv])

    threading.Thread(target=go, daemon=True).start()


@app.get("/api/settings")
def api_settings_get():
    return _json_bytes({"port": PORT})


@app.post("/api/settings")
def api_settings_post():
    body = request.get_json(silent=True) or {}
    port = app_settings.clamp_port(body.get("port"))
    if port is None:
        return _json_bytes(
            {
                "ok": False,
                "error": f"Port must be {app_settings.PORT_MIN}–{app_settings.PORT_MAX}",
                "port": PORT,
            }
        ), 400
    if not _port_available(port):
        return _json_bytes({"ok": False, "error": f"Port {port} is already in use", "port": PORT}), 409
    app_settings.set_port(port)
    restarting = port != PORT
    if restarting:
        _reexec_soon()
    return _json_bytes({"ok": True, "port": port, "restarting": restarting})


@app.get("/api/msfs-autostart")
def api_msfs_autostart_get():
    return _json_bytes(msfs_autostart.status())


@app.post("/api/msfs-autostart")
def api_msfs_autostart_post():
    body = request.get_json(silent=True) or {}
    if "enabled" not in body:
        return _json_bytes({"ok": False, "error": "Missing enabled", **msfs_autostart.status()}), 400
    result = msfs_autostart.set_enabled(bool(body.get("enabled")))
    status = 200 if result.get("ok") else 400
    return _json_bytes(result), status


@app.get("/api/traffic")
def api_traffic():
    return _bytes(_wire.get("raw") or b"{}", "application/json; charset=utf-8", _wire.get("gzip"))


@app.get("/api/airport")
def api_airport():
    icao = request.args.get("icao") or ""
    with _lock:
        aircraft = list(_state.get("aircraft") or [])
    return _json_bytes(airport_payload(icao, aircraft))


@app.get("/api/track")
def api_track():
    aid = request.args.get("id") or ""
    with _lock:
        plane = None
        own = _state.get("ownship")
        if own and own.get("id") == aid:
            plane = dict(own)
        else:
            for row in _state.get("aircraft") or []:
                if row.get("id") == aid:
                    plane = dict(row)
                    break
    paths = _paths.get(aid) or {}
    if plane and not paths.get("sidPath") and not paths.get("starPath"):
        ensure_flightplan_paths(plane)
        paths = {
            "sidPath": plane.get("sidPath") or [],
            "starPath": plane.get("starPath") or [],
        }
    live = _tracks.get(aid)
    first = _tracks.first_seen(aid)
    recon_from = trail_recon.recon_anchor(plane, first, paths)
    points, recon_count = trail_recon.combined_trail(aid, plane, paths, live, recon_from)
    return _json_bytes(
        {
            "id": aid,
            "points": points,
            "reconCount": recon_count,
            "firstSeen": first,
            "reconFrom": recon_from,
        }
    )


@app.get("/api/weather")
def api_weather():
    try:
        lat = float(request.args.get("lat") or "0")
        lon = float(request.args.get("lon") or "0")
    except ValueError:
        lat = lon = 0.0
    alt_ft = None
    try:
        raw_alt = request.args.get("alt") or ""
        if raw_alt != "":
            alt_ft = float(raw_alt)
    except ValueError:
        alt_ft = None
    ground = (request.args.get("ground") or "") in ("1", "true", "yes")
    try:
        wx = weather_enroute(lat, lon) if ground else weather_current(lat, lon, alt_ft)
    except Exception:
        wx = {"tempC": None, "code": None, "windKt": None, "windFrom": None, "qnh": None, "cities": []}
    return _json_bytes(wx)


@app.get("/tiles/<int:z>/<int:x>/<y>")
def api_tiles(z: int, x: int, y: str):
    if not y.endswith(".png"):
        return Response(b"tile", status=404)
    try:
        ynum = int(y[:-4])
    except ValueError:
        return Response(b"tile", status=404)
    payload = tile_png(z, x, ynum, current_version())
    if not payload:
        resp = Response(b"tile", status=503)
        resp.headers["Retry-After"] = "1"
        return resp
    resp = Response(payload, mimetype="image/png")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@app.get("/logo/<name>")
def api_logo(name: str):
    if not name.endswith(".png"):
        return Response(b"logo", status=404)
    payload = logo_png(name[:-4], current_version())
    if payload is None:
        return Response(b"logo", status=404)
    return Response(payload, mimetype="image/png")


@app.get("/<path:name>")
def static_page(name: str):
    resp = send_from_directory(WEB_DIR, name)
    if request.args.get("v") and name.endswith((".js", ".css")):
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    else:
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.after_request
def _default_cache(resp: Response):
    if "Cache-Control" not in resp.headers:
        resp.headers["Cache-Control"] = "no-store"
    return resp


def main() -> None:
    if not WEB_DIR.is_dir():
        raise SystemExit(f"Ordner fehlt: {WEB_DIR}")

    # MSFS exe.xml (and double-clicks) must not spawn a second Waitress.
    single_instance.claim_or_exit(PORT)
    # Point existing Launch.Addon Path at this EXE after ZIP updates to a new folder.
    msfs_autostart.heal_paths()

    warmup_navdata()
    warmup_airports()
    warmup_airlines()
    warmup_actypes()
    warmup_parking()
    threading.Thread(target=sim_loop, daemon=True).start()
    start_tile_workers()
    ip = lan_ip()
    logging.getLogger("waitress").setLevel(logging.ERROR)
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    print()
    print(f"  BATCRadar  v{current_version()}", flush=True)
    print(f"  PC:     http://127.0.0.1:{PORT}")
    print(f"  Tablet: http://{ip}:{PORT}")
    print()
    print("  Traffic: BeyondATC (41717). Ownship map pose: SimConnect; labels/meta: BATC.")
    print("  Asobo-Static bleibt draussen. Auch Flieger ausserhalb der Render-Distanz.")
    print()
    print("  Fenster offen lassen. Strg+C zum Beenden.")
    print()
    try:
        serve(app, host="0.0.0.0", port=PORT, threads=32, channel_timeout=30, ident="batcradar")
    except KeyboardInterrupt:
        print("\n  Beendet.")


if __name__ == "__main__":
    quiet = False
    try:
        main()
    except SystemExit as exc:
        quiet = int(getattr(exc, "code", 1) or 0) == 0
        raise
    finally:
        if getattr(sys, "frozen", False) and not quiet:
            try:
                input("\n  Taste drücken zum Schließen.")
            except EOFError:
                pass
