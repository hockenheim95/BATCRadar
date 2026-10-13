"""BeyondATC traffic websocket (port 41717)."""

from __future__ import annotations

import asyncio
import json
import threading
import time
import urllib.request

from fids import rewrite_ownship_batc_times

BATC_WS = "ws://127.0.0.1:41717"
BATC_ACARS = "http://127.0.0.1:57698"
EXPIRE_S = 25.0
WX_TTL = 2 * 60
CONNECT_GRACE_S = 18.0
STATIC_MARKERS = ("ASXGS", "ASXGA", "ASX", "STATIC")


def _ok_acars(text: str) -> str:
    t = (text or "").strip()
    low = t.lower()
    if not t or low in ("no_airport_found",) or low.startswith("not found"):
        return ""
    return t


def _acars_get(path: str) -> str:
    try:
        req = urllib.request.Request(BATC_ACARS + path, headers={"User-Agent": "BATCRadar/1"})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            return resp.read().decode("utf-8", "replace").strip()
    except Exception:
        return ""


def _is_static(callsign: str) -> bool:
    cs = (callsign or "").upper().replace("-", "")
    if not cs:
        return True
    if cs.startswith("ASX"):
        return True
    return cs in STATIC_MARKERS


class BatcBridge:
    def __init__(self) -> None:
        self._cond = threading.Condition()
        self.connected = False
        self.last_error = ""
        self._planes: dict[str, tuple[float, dict]] = {}
        self._wx: dict[str, tuple[float, dict]] = {}
        self._wx_busy: set[str] = set()
        self._gen = 0
        self._session = 0
        self._stop = False
        self._connected_at = 0.0
        self._last_aircraft_at = 0.0
        self._ws = None
        self._async_loop: asyncio.AbstractEventLoop | None = None
        self._send_queue: asyncio.Queue[dict] | None = None
        threading.Thread(target=self._thread, daemon=True, name="batc").start()

    def snapshot(self) -> tuple[bool, list[dict], str]:
        now = time.monotonic()
        with self._cond:
            keep: dict[str, tuple[float, dict]] = {}
            out: list[dict] = []
            for key, (seen, plane) in self._planes.items():
                if now - seen <= EXPIRE_S:
                    keep[key] = (seen, plane)
                    out.append(dict(plane))
            self._planes = keep
            return self.connected, out, self.last_error

    def generation(self) -> int:
        with self._cond:
            return self._gen

    def session(self) -> int:
        """Increments on each websocket connect; 0 while disconnected."""
        with self._cond:
            return self._session if self.connected else 0

    def aircraft_feed_age_s(self) -> float | None:
        """Seconds since the last aircraft-update ingest; None if disconnected."""
        with self._cond:
            if not self.connected:
                return None
            now = time.monotonic()
            if self._last_aircraft_at > 0:
                return now - self._last_aircraft_at
            if self._connected_at > 0:
                return now - self._connected_at
            return 0.0

    def kick_if_stale(self, max_s: float) -> bool:
        """Close the websocket when BATC stops pushing aircraft-update (zombie socket)."""
        with self._cond:
            if not self.connected:
                return False
            now = time.monotonic()
            if self._last_aircraft_at > 0:
                age = now - self._last_aircraft_at
            elif self._connected_at > 0:
                if now - self._connected_at < CONNECT_GRACE_S:
                    return False
                age = now - self._connected_at
            else:
                return False
            if age < max_s:
                return False
            ws = self._ws
            loop = self._async_loop
        if ws is None or loop is None:
            return False
        try:
            asyncio.run_coroutine_threadsafe(ws.close(), loop)
        except RuntimeError:
            return False
        return True

    def wait_update(self, since: int, timeout: float) -> bool:
        """Block until a newer aircraft dump than *since*, or timeout / disconnect."""
        deadline = time.monotonic() + max(0.0, timeout)
        with self._cond:
            while self._gen <= since and self.connected:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._cond.wait(timeout=remaining)
            return self._gen > since

    def _note_update(self) -> None:
        self._gen += 1
        self._cond.notify_all()

    def weather(self, icao: str) -> dict:
        code = (icao or "").strip().upper()
        empty = {"metar": "", "atis": ""}
        if len(code) != 4:
            return empty
        now = time.monotonic()
        with self._cond:
            hit = self._wx.get(code)
            if hit and now - hit[0] < WX_TTL:
                return {**hit[1], "pending": False}
            stale = dict(hit[1]) if hit else empty
            pending = True
            if code in self._wx_busy:
                return {**stale, "pending": True}
            self._wx_busy.add(code)
        threading.Thread(target=self._fetch_wx, args=(code,), daemon=True, name="batc-wx").start()
        return {**stale, "pending": pending}

    def _fetch_wx(self, code: str) -> None:
        metar = _acars_get(f"/acars/metar/{code}")
        atis = _acars_get(f"/acars/atis/{code}")
        out = {"metar": _ok_acars(metar), "atis": _ok_acars(atis)}
        with self._cond:
            self._wx[code] = (time.monotonic(), out)
            self._wx_busy.discard(code)

    def request_aircraft_refresh(self) -> None:
        """Ask BeyondATC (ws://127.0.0.1:41717) to push a fresh aircraft-update."""
        loop = self._async_loop
        queue = self._send_queue
        with self._cond:
            if not self.connected or loop is None or queue is None:
                return
        async def _put() -> None:
            try:
                queue.put_nowait({"type": "aircraft-request"})
            except asyncio.QueueFull:
                pass

        try:
            asyncio.run_coroutine_threadsafe(_put(), loop)
        except RuntimeError:
            pass

    def _thread(self) -> None:
        while not self._stop:
            try:
                asyncio.run(self._listen())
            except Exception as exc:
                self.last_error = str(exc)
            with self._cond:
                self.connected = False
                self._planes.clear()
                self._ws = None
                self._connected_at = 0.0
                self._last_aircraft_at = 0.0
                self._cond.notify_all()
            time.sleep(2.0)

    async def _listen(self) -> None:
        import websockets

        self._send_queue = asyncio.Queue(maxsize=8)
        self._async_loop = asyncio.get_running_loop()
        try:
            async with websockets.connect(BATC_WS, open_timeout=5, close_timeout=1, ping_interval=20) as ws:
                with self._cond:
                    self._session += 1
                    self._planes.clear()
                    self.connected = True
                    self.last_error = ""
                    self._connected_at = time.monotonic()
                    self._last_aircraft_at = 0.0
                    self._ws = ws
                    self._cond.notify_all()
                try:
                    self._send_queue.put_nowait({"type": "aircraft-request"})
                except asyncio.QueueFull:
                    pass
                await asyncio.gather(self._recv(ws), self._send(ws))
        finally:
            self._async_loop = None
            self._send_queue = None
            with self._cond:
                self.connected = False
                self._planes.clear()
                self._ws = None
                self._connected_at = 0.0
                self._last_aircraft_at = 0.0
                self._cond.notify_all()

    async def _recv(self, ws) -> None:
        async for raw in ws:
            if self._stop:
                return
            self._ingest(raw)

    async def _send(self, ws) -> None:
        queue = self._send_queue
        if queue is None:
            return
        while not self._stop:
            msg = await queue.get()
            await ws.send(json.dumps(msg, separators=(",", ":")))

    def _ingest(self, raw) -> None:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "ignore")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return
        if not isinstance(data, dict):
            return
        kind = data.get("type")
        now = time.monotonic()
        if kind == "aircraft-remove":
            cs = (data.get("callsign") or "").strip().upper()
            if not cs:
                return
            with self._cond:
                self._planes.pop(cs, None)
                self._note_update()
            return
        if kind != "aircraft-update":
            return
        rows = data.get("aircraft") or []
        with self._cond:
            self._last_aircraft_at = now
            for row in rows:
                if not isinstance(row, dict):
                    continue
                try:
                    self._store_row(row, now)
                except Exception:
                    continue
            self._note_update()

    def _store_row(self, row: dict, now: float) -> None:
        cs = str(row.get("callsign") or "").strip().upper()
        if _is_static(cs):
            return
        plane = {
            "callsign": cs,
            "lat": float(row.get("lat") or 0),
            "lon": float(row.get("lon") or 0),
            "alt": float(row.get("alt") or 0),
            "gs": float(row.get("groundspeed") or row.get("speed") or 0),
            "hdg": float(row.get("heading") or 0) % 360.0,
            "type": str(row.get("type") or ""),
            "state": str(row.get("state") or ""),
            "icaoFrom": str(row.get("icaoFrom") or "").upper(),
            "icaoTo": str(row.get("icaoTo") or "").upper(),
            "timeFrom": str(row.get("timeFrom") or ""),
            "timeTo": str(row.get("timeTo") or ""),
            "livery": str(row.get("livery") or ""),
            "tailNumber": str(row.get("tailNumber") or ""),
            "inSim": bool(row.get("inSim")),
            "onGround": bool(row.get("onGround")),
            "isPlayer": bool(row.get("isPlayer")),
            "status": str(row.get("status") or ""),
            "statusSeverity": str(row.get("statusSeverity") or ""),
            "sid": str(row.get("sid") or ""),
            "star": str(row.get("star") or ""),
            "depRunway": str(row.get("depRunway") or ""),
            "arrRunway": str(row.get("arrRunway") or ""),
            "atDestination": bool(row.get("atDestination")),
            "categoryRaw": str(row.get("category") or ""),
            "lengthMeters": float(row.get("lengthMeters") or 0),
            "freq": str(row.get("freq") or ""),
            "freqName": str(row.get("freqName") or ""),
        }
        if abs(plane["lat"]) < 0.01 and abs(plane["lon"]) < 0.01:
            return
        if plane["isPlayer"]:
            plane["timeFrom"], plane["timeTo"] = rewrite_ownship_batc_times(
                plane["timeFrom"], plane["timeTo"]
            )
        self._planes[cs] = (now, plane)
