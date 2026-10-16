"""Build plane dicts from SimConnect / BeyondATC rows and enrich them."""

from __future__ import annotations

import math
import threading

from actypes import name_of as actype_name_lookup
from airlines import iata_of as airline_iata_lookup
from airlines import name_of as airline_name_lookup
from airports import lookup as airport_lookup
from airports import nearest as nearest_airport
from navdata import runway_pair, sid_points, star_points
from runways import runway_end
from tables import (
    AIRLINE_IATA,
    AIRLINE_IATA_EXTRA,
    AIRPORT_LL,
    AIRPORT_TZ,
    ICAO_PREFIX_TZ,
    AIRLINE_NAMES,
    CALLSIGN_AIRLINE,
    TYPE_NAMES,
    TYPE_NEEDLES,
    TYPE_STYLE,
)


def style_for(icao_type: str) -> tuple[str, float]:
    return TYPE_STYLE.get((icao_type or "").upper(), ("airliner", 1.0))

def pretty_type_name(icao_type: str, fallback: str = "") -> str:
    code = (icao_type or "").strip().upper()
    if not code:
        return fallback or "—"
    if code in TYPE_NAMES:
        return TYPE_NAMES[code]
    looked = actype_name_lookup(code)
    if looked:
        return looked
    text = (fallback or "").strip()
    if text and text.upper() != code:
        return text
    return code

def airline_iata_code(icao: str) -> str:
    code = (icao or "").strip().upper()
    if not code:
        return ""
    return AIRLINE_IATA.get(code) or airline_iata_lookup(code) or AIRLINE_IATA_EXTRA.get(code) or ""

def flight_iata(callsign: str, airline_icao: str = "", iata: str = "") -> str:
    cs = (callsign or "").strip().upper()
    if not cs:
        return ""
    prefix = (airline_icao or "").strip().upper()
    code = (iata or airline_iata_code(prefix)).strip().upper()
    if code and prefix and cs.startswith(prefix):
        return code + cs[len(prefix):]
    letters = "".join(ch for ch in cs if ch.isalpha())[:3]
    if code and letters and cs.startswith(letters):
        return code + cs[len(letters):]
    return cs

def attach_flight_iata(plane: dict) -> None:
    icao = plane.get("airlineIcao") or ""
    iata = plane.get("airlineIata") or airline_iata_code(icao)
    plane["airlineIata"] = iata
    plane["flightIata"] = flight_iata(plane.get("callsign") or "", icao, iata)
    current = (plane.get("airline") or "").strip()
    compact = "".join(ch for ch in current if ch.isalnum()).upper()
    if not current or (2 <= len(compact) <= 3 and compact == current.upper()):
        resolved = airline_name_lookup(icao) or airline_name_lookup(iata) or airline_name_lookup(compact)
        if resolved:
            plane["airline"] = resolved
    icao_type = plane.get("type") or ""
    type_name = (plane.get("typeName") or "").strip()
    compact_type = "".join(ch for ch in type_name if ch.isalnum()).upper()
    if not type_name or compact_type == (icao_type or "").upper():
        pretty = pretty_type_name(icao_type, type_name)
        if pretty:
            plane["typeName"] = pretty

def infer_type(model: str, title: str) -> str:
    blob = f"{model} {title}".upper()
    for needle, code in TYPE_NEEDLES:
        if needle in blob:
            return code
    model = (model or "").strip().upper()
    if 2 <= len(model) <= 6 and model.isalnum():
        return model
    return "A20N"

def _airline_code_name(code: str, fallback: str = "") -> str:
    return airline_name_lookup(code) or fallback or code

def map_airline(raw: str) -> tuple[str, str]:
    text = (raw or "").strip()
    low = text.lower()
    for key, icao, name in AIRLINE_NAMES:
        if key in low:
            return icao, name
    compact = "".join(ch for ch in text if ch.isalpha()).upper()
    if 2 <= len(compact) <= 3:
        return compact, _airline_code_name(compact, text)
    resolved = airline_name_lookup(text.upper()) if text.isalpha() and 2 <= len(text) <= 3 else ""
    return "", resolved or text or "—"

def airline_from_callsign(callsign: str, livery: str = "") -> tuple[str, str]:
    letters = "".join(ch for ch in (callsign or "") if ch.isalpha())[:3].upper()
    if letters in CALLSIGN_AIRLINE:
        return CALLSIGN_AIRLINE[letters]
    icao, name = map_airline(livery)
    if icao:
        return icao, _airline_code_name(icao, name)
    if letters:
        return letters, _airline_code_name(letters, letters)
    return "", "—"

def place_or_empty(code: str, kind: str) -> dict:
    ap = airport_lookup(code)
    if not ap:
        return {
            kind: "",
            f"{kind}Iata": "",
            f"{kind}Name": "",
        }
    icao, iata, name, lat, lon, tz = ap
    fields = {
        kind: icao,
        f"{kind}Iata": iata,
        f"{kind}Name": name,
        f"{kind}Tz": tz or tz_fallback(icao, iata, lat, lon),
    }
    if lat is None or lon is None:
        known = AIRPORT_LL.get((icao or "").upper()) or AIRPORT_LL.get((iata or "").upper())
        if known:
            lat, lon = known
    if lat is not None and lon is not None:
        fields[f"{kind}Lat"] = lat
        fields[f"{kind}Lon"] = lon
    return fields


def tz_fallback(icao: str, iata: str = "", lat: float | None = None, lon: float | None = None) -> str:
    """Timezone for airports whose database row has none."""
    icao = (icao or "").upper()
    iata = (iata or "").upper()
    if AIRPORT_TZ.get(icao):
        return AIRPORT_TZ[icao]
    if AIRPORT_TZ.get(iata):
        return AIRPORT_TZ[iata]
    if len(icao) == 4:
        if icao.startswith("LP"):
            return _tz_portugal(lat, lon)
        return ICAO_PREFIX_TZ.get(icao[:2], "")
    return ""


def _tz_portugal(lat: float | None, lon: float | None) -> str:
    if lon is not None and float(lon) <= -23:
        return "Atlantic/Azores"
    if lon is not None and float(lon) <= -15.4 and (lat is None or float(lat) <= 34.8):
        return "Atlantic/Madeira"
    return "Europe/Lisbon"

_fp_lock = threading.Lock()
_path_geom_cache: dict[tuple, dict] = {}


def flightplan_path_key(origin: str, dest: str, plane: dict, extra: dict | None) -> tuple:
    o = (origin or plane.get("origin") or "").upper()
    d = (dest or plane.get("dest") or "").upper()
    ex = extra or {}
    sid = str(ex.get("sid") or plane.get("sid") or "")
    star = str(ex.get("star") or plane.get("star") or "")
    dep = str(ex.get("depRunway") or plane.get("depRunway") or "")
    arr = str(ex.get("arrRunway") or plane.get("arrRunway") or "")
    geo = ""
    if o and not dep:
        lat = plane.get("lat")
        lon = plane.get("lon")
        if lat is not None and lon is not None:
            geo = f"{round(float(lat), 2)}:{round(float(lon), 2)}"
    return (o, d, sid, star, dep, arr, geo)


def clear_flightplan_path_cache() -> None:
    with _fp_lock:
        _path_geom_cache.clear()


def _compute_flightplan_paths(
    plane: dict, origin: str, dest: str, extra: dict | None
) -> dict:
    out: dict = {
        "depRwyLat": None,
        "depRwyLon": None,
        "depRwyHdg": None,
        "depRwyEndLat": None,
        "depRwyEndLon": None,
        "sidPath": [],
        "starPath": [],
    }
    if not extra:
        return out
    lat = plane.get("lat")
    lon = plane.get("lon")
    hdg = plane.get("hdg")
    dep_rwy = extra.get("depRunway") or plane.get("depRunway") or ""
    start, far = runway_pair(origin, dep_rwy, hdg, lat, lon)
    if not start:
        rwy = runway_end(origin, dep_rwy, hdg, lat, lon)
        if rwy:
            start = ("", rwy[0], rwy[1], rwy[2])
    if start:
        out["depRwyLat"], out["depRwyLon"], out["depRwyHdg"] = start[1], start[2], start[3]
    if far:
        out["depRwyEndLat"], out["depRwyEndLon"] = far[1], far[2]
    path = sid_points(
        origin,
        extra.get("sid") or plane.get("sid") or "",
        dep_rwy,
        lat,
        lon,
        hdg,
    )
    if path:
        out["sidPath"] = path
    arrival = star_points(
        dest,
        extra.get("star") or plane.get("star") or "",
        extra.get("arrRunway") or plane.get("arrRunway") or "",
        lat,
        lon,
        hdg,
    )
    if arrival:
        out["starPath"] = arrival
    return out


def ensure_flightplan_paths(
    plane: dict,
    origin: str = "",
    dest: str = "",
    extra: dict | None = None,
) -> None:
    """SID/STAR geometry from cache; recompute only when the flight-plan key changes."""
    o = origin or plane.get("origin") or ""
    d = dest or plane.get("dest") or ""
    ex = extra if extra is not None else plane
    key = flightplan_path_key(o, d, plane, ex)
    with _fp_lock:
        geom = _path_geom_cache.get(key)
        if geom is None:
            geom = _compute_flightplan_paths(plane, o, d, ex)
            _path_geom_cache[key] = geom
    for field, val in geom.items():
        if field.startswith("depRwy") and val is None:
            continue
        if val:
            plane[field] = val
        elif field in ("sidPath", "starPath"):
            plane.pop(field, None)


def apply_flightplan(
    plane: dict,
    origin: str,
    dest: str,
    extra: dict | None = None,
    *,
    paths: bool = False,
) -> None:
    if origin:
        plane.update(place_or_empty(origin, "origin"))
    if dest:
        plane.update(place_or_empty(dest, "dest"))
    if extra:
        for key in ("sid", "star", "depRunway", "arrRunway", "timeFrom", "timeTo", "state", "freq", "freqName", "status"):
            if extra.get(key):
                plane[key] = extra[key]
        if "onGround" in extra:
            plane["onGround"] = bool(extra.get("onGround"))
        if "atDestination" in extra:
            plane["atDestination"] = bool(extra.get("atDestination"))
    if paths and extra:
        ensure_flightplan_paths(plane, origin, dest, extra)


def plane_from_batc(raw: dict, *, paths: bool = False) -> dict:
    icao_type = infer_type(raw.get("type") or "", raw.get("livery") or "")
    cat, scale = style_for(icao_type)
    length = float(raw.get("lengthMeters") or 0)
    if length:
        scale = max(0.42, min(2.05, length / 38.0))
    blob = (raw.get("categoryRaw") or "").lower()
    if "prop" in blob or "piston" in blob:
        cat = "prop"
    elif "biz" in blob:
        cat = "bizjet"
    airline_icao, airline_name = airline_from_callsign(raw.get("callsign") or "", raw.get("livery") or "")
    cs = raw.get("callsign") or "BATC"
    plane = {
        "id": f"batc-{cs}",
        "callsign": cs,
        "flight": cs,
        "type": icao_type,
        "typeName": pretty_type_name(icao_type, raw.get("type") or icao_type),
        "airline": airline_name,
        "airlineIcao": airline_icao,
        "airlineIata": AIRLINE_IATA.get(airline_icao, ""),
        "registration": raw.get("tailNumber") or "",
        "lat": raw["lat"],
        "lon": raw["lon"],
        "alt": raw["alt"],
        "gs": raw["gs"],
        "hdg": raw["hdg"],
        "isOwnship": False,
        "source": "batc",
        "category": cat,
        "sizeScale": scale,
        "inSim": bool(raw.get("inSim")),
        "onGround": bool(raw.get("onGround")),
        "atDestination": bool(raw.get("atDestination")),
        "state": str(raw.get("state") or ""),
        "status": str(raw.get("status") or ""),
        "freq": str(raw.get("freq") or ""),
        "freqName": str(raw.get("freqName") or ""),
        "depRunway": str(raw.get("depRunway") or ""),
        "arrRunway": str(raw.get("arrRunway") or ""),
        "title": raw.get("livery") or "",
    }
    apply_flightplan(plane, raw.get("icaoFrom") or "", raw.get("icaoTo") or "", raw, paths=paths)
    snap_origin_to_field(plane)
    return plane

def _on_field(plane: dict) -> bool:
    if plane.get("onGround") or plane.get("atDestination"):
        return True
    st = (plane.get("state") or "").lower()
    if st in ("parked", "taxi", "pushback"):
        return True
    return float(plane.get("gs") or 0) < 40 and float(plane.get("alt") or 0) < 2500

def _haversine_field(lat: float, lon: float, alat: float, alon: float) -> float:
    dlat = math.radians(alat - lat)
    dlon = math.radians(alon - lon)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat)) * math.cos(math.radians(alat)) * math.sin(dlon / 2) ** 2
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(a)))

def snap_origin_to_field(plane: dict) -> None:
    """Use the airport under the aircraft when GPS/BATC origin is a nearby field (ownship only)."""
    if not plane.get("isOwnship"):
        return
    if not _on_field(plane) or plane.get("atDestination"):
        return
    lat = plane.get("lat")
    lon = plane.get("lon")
    if lat is None or lon is None:
        return
    near = nearest_airport(lat, lon, 8.0)
    if not near:
        return
    icao, _iata, _name, alat, alon, _tz = near
    dest = (plane.get("dest") or "").upper()
    if dest == icao:
        return
    origin = (plane.get("origin") or "").upper()
    if origin == icao:
        return
    if origin and plane.get("originLat") is not None and plane.get("originLon") is not None:
        d_orig = _haversine_field(lat, lon, plane["originLat"], plane["originLon"])
        d_near = _haversine_field(lat, lon, alat, alon) if alat is not None else 0.0
        if d_orig <= d_near + 1.5:
            return
    plane.update(place_or_empty(icao, "origin"))
    for key in (
        "sid",
        "sidPath",
        "depRunway",
        "depRwyLat",
        "depRwyLon",
        "depRwyHdg",
        "depRwyEndLat",
        "depRwyEndLon",
    ):
        plane.pop(key, None)

_OWN_GS_NOISE_KT = 10.0
_OWN_GS_PARK_BRAKE_MAX_KT = 30.0
_OWN_GS_PARKED_MAX_KT = 15.0


def normalize_ownship_gs(plane: dict) -> None:
    """Drop MSFS ground-velocity jitter while parked; keep taxi/pushback speeds."""
    if not plane.get("isOwnship"):
        return
    gs = float(plane.get("gs") or 0)
    if gs <= 0:
        plane["gs"] = 0.0
        return
    st = (plane.get("state") or "").lower()
    if st in ("taxi", "pushback", "takeoff", "flight"):
        return
    if plane.get("parkingBrake") and gs < _OWN_GS_PARK_BRAKE_MAX_KT:
        plane["gs"] = 0.0
        return
    if st == "parked" and gs < _OWN_GS_PARKED_MAX_KT:
        plane["gs"] = 0.0
        return
    standing = bool(plane.get("parkingBrake") or plane.get("gate") or st == "parked")
    if standing and plane.get("onGround") and gs < _OWN_GS_NOISE_KT:
        plane["gs"] = 0.0


def plane_from_sim(raw: dict, is_own: bool) -> dict:
    icao_type = infer_type(raw.get("model") or "", raw.get("title") or "")
    cat, scale = style_for(icao_type)
    airline_icao, airline_name = map_airline(raw.get("airline") or "")
    atc_id = (raw.get("atc_id") or "").strip()
    number = (raw.get("flight_number") or "").strip()
    callsign = atc_id or (f"{airline_icao}{number}" if airline_icao and number else "MSFS")
    flight = f"{airline_icao}{number}" if airline_icao and number else callsign
    if is_own:
        origin_code = raw.get("gps_origin") or ""
        dest_code = raw.get("gps_dest") or ""
    else:
        origin_code = dest_code = ""
    gs = float(raw.get("gs") or 0)
    alt = float(raw.get("alt") or 0)
    if "simOnGround" in raw:
        on_ground = bool(raw.get("simOnGround"))
    else:
        on_ground = gs < 40 and alt < 8000
    plane = {
        "id": "ownship" if is_own else f"sc-{raw.get('object_id')}",
        "callsign": callsign,
        "flight": flight,
        "type": icao_type,
        "typeName": pretty_type_name(icao_type, raw.get("title") or icao_type),
        "airline": airline_name if airline_name != "—" else (raw.get("title") or "MSFS"),
        "airlineIcao": airline_icao,
        "airlineIata": AIRLINE_IATA.get(airline_icao, ""),
        **place_or_empty(origin_code, "origin"),
        **place_or_empty(dest_code, "dest"),
        "registration": atc_id,
        "lat": raw["lat"],
        "lon": raw["lon"],
        "alt": raw["alt"],
        "gs": raw["gs"],
        "hdg": raw["hdg"],
        "isOwnship": is_own,
        "source": "simconnect",
        "category": cat,
        "sizeScale": scale,
        "title": raw.get("title") or "",
        "onGround": on_ground,
    }
    if is_own:
        plane["simOnGround"] = bool(raw.get("simOnGround"))
        plane["parkingBrake"] = bool(raw.get("parkingBrake"))
    return plane
