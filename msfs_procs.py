"""SID/STAR from the running sim via SimConnect Facility Data (the same source BeyondATC uses).

Whatever AIRAC cycle MSFS has loaded (e.g. the Navigraph update) is what we get here, so no
external navdata database is needed while the sim runs. Results are cached on disk per airport
and refreshed when the MSFS navdata cycle changes.

Also provides airport position and runways (the sim's own facility data), so no external navdata
database is involved at all.

Public API (non-blocking, safe to call every tick):
    start()            start the background session
    status(icao)       "ready" | "pending" | "none"
    airport(icao)      cached payload {airport, runways, sids, stars, ...} or None
"""

from __future__ import annotations

import ctypes
import json
import math
import queue
import struct
import threading
import time
from ctypes import POINTER, byref
from ctypes.wintypes import DWORD, HANDLE
from pathlib import Path

from SimConnect.Enum import SIMCONNECT_RECV, SIMCONNECT_RECV_ID, SIMCONNECT_RECV_EXCEPTION

import simconnect_bridge as sb
from paths import root

CACHE_DIR = root() / "data" / "procs"
_PKG_SUBS = ("Community", "Community2024", "Official2024", "Official2020")

DEF_DEP = 40
DEF_ARR = 41
DEF_APT = 42
REQ_BASE = 5000
RECV_FACILITY_DATA = 28
RECV_FACILITY_DATA_END = 29

T_AIRPORT = 0
T_RUNWAY = 1
T_PARKING = 15
T_DEPARTURE = 10
T_ARRIVAL = 11
T_RWY_TRANS = 12
T_ENR_TRANS = 13
T_LEG = 7

REQUEST_TIMEOUT_S = 20.0
RETRY_AFTER_S = 300.0

LEG_TYPES = {
    1: "AF", 2: "CA", 3: "CD", 4: "CF", 5: "CI", 6: "CR", 7: "DF", 8: "FA", 9: "FC",
    10: "FD", 11: "FM", 12: "HA", 13: "HF", 14: "HM", 15: "IF", 16: "PI", 17: "RF",
    18: "TF", 19: "VA", 20: "VD", 21: "VI", 22: "VM", 23: "VR",
}
DESIGNATOR = {0: "", 1: "L", 2: "R", 3: "C", 4: "W", 5: "A", 6: "B"}

_LEG = (
    "OPEN APPROACH_LEG",
    "TYPE",
    "FIX_ICAO",
    "FIX_LATITUDE",
    "FIX_LONGITUDE",
    "COURSE",
    "ROUTE_DISTANCE",
    "CLOSE APPROACH_LEG",
)


APT_DEFINITION = [
    "OPEN AIRPORT",
    "LATITUDE",
    "LONGITUDE",
    "ALTITUDE",
    "NAME64",
    "OPEN RUNWAY",
    "LATITUDE",
    "LONGITUDE",
    "ALTITUDE",
    "HEADING",
    "LENGTH",
    "WIDTH",
    "PRIMARY_NUMBER",
    "PRIMARY_DESIGNATOR",
    "SECONDARY_NUMBER",
    "SECONDARY_DESIGNATOR",
    "CLOSE RUNWAY",
    "OPEN TAXI_PARKING",
    "BIAS_X",
    "BIAS_Z",
    "HEADING",
    "NAME",
    "SUFFIX",
    "NUMBER",
    "TYPE",
    "CLOSE TAXI_PARKING",
    "CLOSE AIRPORT",
]


def _definition(kind: str) -> list[str]:
    return [
        "OPEN AIRPORT",
        f"OPEN {kind}",
        "NAME",
        "N_RUNWAY_TRANSITIONS",
        "N_ENROUTE_TRANSITIONS",
        "N_APPROACH_LEGS",
        "OPEN RUNWAY_TRANSITION",
        "RUNWAY_NUMBER",
        "RUNWAY_DESIGNATOR",
        "N_APPROACH_LEGS",
        *_LEG,
        "CLOSE RUNWAY_TRANSITION",
        "OPEN ENROUTE_TRANSITION",
        "NAME",
        "N_APPROACH_LEGS",
        *_LEG,
        "CLOSE ENROUTE_TRANSITION",
        *_LEG,
        f"CLOSE {kind}",
        "CLOSE AIRPORT",
    ]


def msfs_cycle() -> str:
    base = Path.home() / "AppData" / "Roaming" / "Microsoft Flight Simulator 2024" / "Packages"
    for sub in _PKG_SUBS:
        path = base / sub / "navigraph-nav-base" / "ContentInfo" / "navigraph-navdata" / "cycle.json"
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return ""
            return str(data.get("Cycle") or data.get("cycle") or "")
    return ""


def _cstr(raw: bytes) -> str:
    return raw.split(b"\x00", 1)[0].decode("ascii", "ignore").strip()


def _park_letter(code: int) -> str:
    if 12 <= code <= 37:
        return chr(ord("A") + code - 12)
    return {1: "", 2: "N", 3: "NE", 4: "E", 5: "SE", 6: "S", 7: "SW", 8: "W", 9: "NW", 10: "G", 11: "D"}.get(
        code, ""
    )


def _parkings_ll(apt: dict | None, rows: list) -> list[dict]:
    """Convert SimConnect TAXI_PARKING bias (metres from ARP) to lat/lon + gate label."""
    if not apt:
        return []
    alat, alon = float(apt["lat"]), float(apt["lon"])
    east = 111319.9 * max(0.2, math.cos(math.radians(alat)))
    out: list[dict] = []
    for bx, bz, _hdg, name, suf, num, ptype in rows:
        if ptype in (0, 12, 13):
            continue
        letter = _park_letter(int(name))
        extra = _park_letter(int(suf))
        n = int(num)
        label = f"{letter}{n if n else ''}{extra}"
        if not label:
            continue
        out.append(
            {
                "lat": alat + float(bz) / 111319.9,
                "lon": alon + float(bx) / east,
                "name": label,
                "type": int(ptype),
            }
        )
    return out


def _leg(data: bytes) -> dict | None:
    if len(data) < 36:
        return None
    typ, icao, lat, lon, course, dist = struct.unpack_from("<i8sddff", data, 0)
    return {
        "type": LEG_TYPES.get(typ, ""),
        "fix": _cstr(icao),
        "lat": lat,
        "lon": lon,
        "course": course,
        "dist": dist,
    }


class _Client:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queue: queue.Queue[str] = queue.Queue()
        self._data: dict[str, dict] = {}
        self._state: dict[str, str] = {}
        self._failed_at: dict[str, float] = {}
        self._asked_at: dict[str, float] = {}
        self._jobs: dict[int, dict] = {}
        self._next_rid = REQ_BASE
        self.connected = False
        self._started = False
        self._cycle = ""
        self._logged_exc = False

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
        threading.Thread(target=self._loop, daemon=True, name="msfs-procs").start()

    # --- public ---

    def status(self, icao: str) -> str:
        code = (icao or "").strip().upper()
        if len(code) != 4:
            return "none"
        with self._lock:
            state = self._state.get(code)
            if state == "ready":
                return "ready"
            if state == "pending":
                return "pending"
            failed = self._failed_at.get(code, 0.0)
            connected = self.connected
        if state is None:
            disk = self._read_disk(code)
            if disk is not None and (not connected or disk.get("cycle") == self._current_cycle()):
                with self._lock:
                    self._data[code] = disk
                    self._state[code] = "ready"
                return "ready"
        if not connected:
            return "none"
        if failed and time.monotonic() - failed < RETRY_AFTER_S:
            return "none"
        with self._lock:
            self._state[code] = "pending"
            self._asked_at[code] = time.monotonic()
        self._queue.put(code)
        return "pending"

    def payload(self, icao: str) -> dict | None:
        code = (icao or "").strip().upper()
        if self.status(code) != "ready":
            return None
        with self._lock:
            return self._data.get(code)

    # --- helpers ---

    def _current_cycle(self) -> str:
        if not self._cycle:
            self._cycle = msfs_cycle()
        return self._cycle

    @staticmethod
    def _read_disk(code: str) -> dict | None:
        path = CACHE_DIR / f"{code}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict) or not data.get("airport") or "sids" not in data or "parkings" not in data:
            return None
        return data

    def _write_disk(self, code: str, payload: dict) -> None:
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            (CACHE_DIR / f"{code}.json").write_text(
                json.dumps(payload, separators=(",", ":")), encoding="utf-8"
            )
        except OSError:
            pass

    # --- session ---

    def _loop(self) -> None:
        while True:
            try:
                self._session()
            except Exception as exc:  # noqa: BLE001
                print(f"  Prozeduren (SimConnect): {exc}", flush=True)
            with self._lock:
                self.connected = False
                for code, state in list(self._state.items()):
                    if state == "pending":
                        del self._state[code]
                self._jobs.clear()
            while True:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    break
            time.sleep(5.0)

    def _session(self) -> None:
        dll = ctypes.WinDLL(sb._dll_path())
        sb._bind(dll)
        handle = HANDLE()
        hr = dll.SimConnect_Open(byref(handle), b"BATCRadar Procedures", None, 0, None, 0)
        if not sb._ok(hr):
            return
        pp = POINTER(SIMCONNECT_RECV)()
        cb = DWORD()
        opened = False
        deadline = time.monotonic() + 12.0
        while time.monotonic() < deadline:
            hr = dll.SimConnect_GetNextDispatch(handle, byref(pp), byref(cb))
            if sb._hr(hr) != sb.S_OK:
                time.sleep(0.05)
                continue
            if pp.contents.dwID == SIMCONNECT_RECV_ID.SIMCONNECT_RECV_ID_OPEN:
                opened = True
                break
            if pp.contents.dwID == SIMCONNECT_RECV_ID.SIMCONNECT_RECV_ID_QUIT:
                dll.SimConnect_Close(handle)
                return
        if not opened:
            dll.SimConnect_Close(handle)
            return

        for def_id, fields in (
            (DEF_DEP, _definition("DEPARTURE")),
            (DEF_ARR, _definition("ARRIVAL")),
            (DEF_APT, APT_DEFINITION),
        ):
            for field in fields:
                dll.SimConnect_AddToFacilityDefinition(handle, def_id, field.encode("ascii"))

        with self._lock:
            self.connected = True
        self._cycle = msfs_cycle()
        print("  Prozeduren: SimConnect Facility Data verbunden (MSFS-Navdata).", flush=True)

        try:
            while True:
                self._drain(dll, handle)
                self._expire()
                hr = dll.SimConnect_GetNextDispatch(handle, byref(pp), byref(cb))
                code = sb._hr(hr)
                if code != sb.S_OK:
                    time.sleep(0.01)
                    continue
                msg_id = pp.contents.dwID
                if msg_id == SIMCONNECT_RECV_ID.SIMCONNECT_RECV_ID_QUIT:
                    return
                if msg_id == RECV_FACILITY_DATA:
                    self._on_record(pp)
                elif msg_id == RECV_FACILITY_DATA_END:
                    self._on_end(pp)
                elif msg_id == SIMCONNECT_RECV_ID.SIMCONNECT_RECV_ID_EXCEPTION and not self._logged_exc:
                    self._logged_exc = True
                    exc = ctypes.cast(pp, POINTER(SIMCONNECT_RECV_EXCEPTION)).contents
                    print(f"  Prozeduren: SimConnect Exception {int(exc.dwException)}", flush=True)
        finally:
            try:
                dll.SimConnect_Close(handle)
            except OSError:
                pass

    def _drain(self, dll, handle) -> None:
        while True:
            try:
                icao = self._queue.get_nowait()
            except queue.Empty:
                return
            for def_id, key in ((DEF_DEP, "sids"), (DEF_ARR, "stars"), (DEF_APT, "apt")):
                rid = self._next_rid
                self._next_rid += 1
                self._jobs[rid] = {
                    "icao": icao,
                    "key": key,
                    "nodes": {},
                    "list": [],
                    "airport": None,
                    "runways": [],
                    "parkings": [],
                    "done": False,
                }
                hr = dll.SimConnect_RequestFacilityData(handle, def_id, rid, icao.encode("ascii"), None)
                if not sb._ok(hr):
                    self._fail(icao)
                    break

    def _expire(self) -> None:
        now = time.monotonic()
        with self._lock:
            late = [
                code
                for code, state in self._state.items()
                if state == "pending" and now - self._asked_at.get(code, now) > REQUEST_TIMEOUT_S
            ]
        for code in late:
            self._fail(code)

    def _fail(self, icao: str) -> None:
        with self._lock:
            self._state.pop(icao, None)
            self._failed_at[icao] = time.monotonic()
            for rid in [r for r, job in self._jobs.items() if job["icao"] == icao]:
                del self._jobs[rid]

    def _on_record(self, pp) -> None:
        size = pp.contents.dwSize
        if size < 40:
            return
        raw = ctypes.string_at(ctypes.addressof(pp.contents), size)
        rid, uniq, parent, typ = struct.unpack_from("<4I", raw, 12)
        job = self._jobs.get(rid)
        if job is None:
            return
        data = raw[40:]
        nodes = job["nodes"]
        if job["key"] == "apt":
            if typ == T_AIRPORT and len(data) >= 88:
                lat, lon, alt, name = struct.unpack_from("<3d64s", data, 0)
                job["airport"] = {"lat": lat, "lon": lon, "alt": alt, "name": _cstr(name)}
            elif typ == T_RUNWAY and len(data) >= 52:
                lat, lon, _alt, hdg, length, width, pn, pd, sn, sd = struct.unpack_from("<3d3f4i", data, 0)
                job["runways"].append(
                    {
                        "lat": lat,
                        "lon": lon,
                        "hdg": hdg,
                        "len": length,
                        "wid": width,
                        "pri": f"{pn:02d}{DESIGNATOR.get(pd, '')}",
                        "sec": f"{sn:02d}{DESIGNATOR.get(sd, '')}",
                    }
                )
            elif typ == T_PARKING and len(data) >= 28:
                job["parkings"].append(struct.unpack_from("<3f4i", data, 0))
            return
        if typ in (T_DEPARTURE, T_ARRIVAL):
            if len(data) < 8:
                return
            node = {"name": _cstr(data[:8]), "rwy": [], "enr": [], "legs": []}
            job["list"].append(node)
            nodes[uniq] = node
        elif typ == T_RWY_TRANS:
            if len(data) < 12:
                return
            num, des, _n = struct.unpack_from("<3i", data, 0)
            node = {"rwy": f"{num:02d}{DESIGNATOR.get(des, '')}", "legs": []}
            owner = nodes.get(parent)
            if owner is not None:
                owner["rwy"].append(node)
            nodes[uniq] = node
        elif typ == T_ENR_TRANS:
            if len(data) < 12:
                return
            node = {"name": _cstr(data[:8]), "legs": []}
            owner = nodes.get(parent)
            if owner is not None:
                owner["enr"].append(node)
            nodes[uniq] = node
        elif typ == T_LEG:
            leg = _leg(data)
            owner = nodes.get(parent)
            if leg is not None and owner is not None:
                owner["legs"].append(leg)

    def _on_end(self, pp) -> None:
        if pp.contents.dwSize < 16:
            return
        raw = ctypes.string_at(ctypes.addressof(pp.contents), 16)
        rid = struct.unpack_from("<I", raw, 12)[0]
        job = self._jobs.get(rid)
        if job is None:
            return
        job["done"] = True
        icao = job["icao"]
        mates = [j for j in self._jobs.values() if j["icao"] == icao]
        if not all(j["done"] for j in mates):
            return
        payload: dict = {"icao": icao, "cycle": self._current_cycle(), "fetched": int(time.time())}
        for j in mates:
            if j["key"] == "apt":
                payload["airport"] = j["airport"]
                payload["runways"] = j["runways"]
                payload["parkings"] = _parkings_ll(j.get("airport"), j.get("parkings") or [])
            else:
                payload[j["key"]] = j["list"]
        payload.setdefault("sids", [])
        payload.setdefault("stars", [])
        payload.setdefault("runways", [])
        payload.setdefault("parkings", [])
        for rid2 in [r for r, j in self._jobs.items() if j["icao"] == icao]:
            del self._jobs[rid2]
        apt = payload.get("airport")
        if not apt or (not apt.get("lat") and not apt.get("lon")):
            self._fail(icao)
            return
        self._write_disk(icao, payload)
        with self._lock:
            self._data[icao] = payload
            self._state[icao] = "ready"


_client = _Client()


def start() -> None:
    _client.start()


def status(icao: str) -> str:
    return _client.status(icao)


def airport(icao: str) -> dict | None:
    return _client.payload(icao)
