"""Direct MSFS SimConnect client: ownship, weather, runway, airport names."""

from __future__ import annotations

import ctypes
import json
import queue
import threading
import time
from ctypes import POINTER, Structure, byref, sizeof
from ctypes.wintypes import DWORD, HANDLE, HWND, LPCSTR
from pathlib import Path

from SimConnect.Enum import (
    SIMCONNECT_DATATYPE,
    SIMCONNECT_PERIOD,
    SIMCONNECT_RECV,
    SIMCONNECT_RECV_EXCEPTION,
    SIMCONNECT_RECV_ID,
    SIMCONNECT_RECV_SIMOBJECT_DATA,
)
from SimConnect.SimConnect import _library_path

from paths import bundle, data_file, root

DLL_CANDIDATES = [
    bundle() / "native" / "SimConnect.dll",
    root() / "native" / "SimConnect.dll",
    Path(r"D:\Users\Anwender\Documents\msfs\MSFS_Addons_Linker\Simconnect_2024\SimConnect.dll"),
    Path(r"D:\MSFS SDK\SimConnect SDK\lib\SimConnect.dll"),
    Path(_library_path),
]

S_OK = 0
E_FAIL = 0x80004005

DEF_AC = 0
REQ_USER = 0

DEF_GPS = 1
REQ_GPS = 3

DEF_WX = 2
REQ_WX = 4

DEF_RWY = 3
REQ_RWY = 5

DEF_POSE = 4
REQ_POSE = 6

REQ_METAR_NEAR = 20
REQ_METAR_STN = 21
RECV_WEATHER_OBS = int(SIMCONNECT_RECV_ID.SIMCONNECT_RECV_ID_WEATHER_OBSERVATION)

DEF_FAC = 10
REQ_FAC_BASE = 1000
RECV_FACILITY_DATA = 28
FAC_HEADER = 12 + 28  # SIMCONNECT_RECV + facility fields before Data


class AircraftBlob(Structure):
    _pack_ = 1
    _fields_ = [
        ("lat", ctypes.c_double),
        ("lon", ctypes.c_double),
        ("alt", ctypes.c_double),
        ("gs", ctypes.c_double),
        ("hdg", ctypes.c_double),
        ("on_ground", ctypes.c_double),
        ("park_brake", ctypes.c_double),
        ("atc_id", ctypes.c_char * 32),
        ("airline", ctypes.c_char * 32),
        ("flight_number", ctypes.c_char * 32),
        ("model", ctypes.c_char * 32),
        ("title", ctypes.c_char * 256),
    ]


class PoseBlob(Structure):
    _pack_ = 1
    _fields_ = [
        ("lat", ctypes.c_double),
        ("lon", ctypes.c_double),
        ("alt", ctypes.c_double),
        ("gs", ctypes.c_double),
        ("hdg", ctypes.c_double),
        ("on_ground", ctypes.c_double),
        ("park_brake", ctypes.c_double),
    ]


class GpsBlob(Structure):
    _pack_ = 1
    _fields_ = [
        ("prev_id", ctypes.c_char * 32),
        ("next_id", ctypes.c_char * 32),
    ]


class WxBlob(Structure):
    _pack_ = 1
    _fields_ = [
        ("wind_from", ctypes.c_double),
        ("wind_kt", ctypes.c_double),
        ("temp_c", ctypes.c_double),
        ("dew_c", ctypes.c_double),
        ("qnh", ctypes.c_double),
        ("vis_m", ctypes.c_double),
    ]


class RwyBlob(Structure):
    _pack_ = 1
    _fields_ = [
        ("selected", ctypes.c_char * 32),
        ("airport", ctypes.c_char * 32),
    ]


def _blob_bytes(msg: SIMCONNECT_RECV_SIMOBJECT_DATA, size: int) -> bytes:
    return ctypes.string_at(ctypes.addressof(msg.dwData), size)


def _cstr(raw: bytes) -> str:
    return raw.split(b"\x00", 1)[0].decode("utf-8", "ignore").strip()


def _ok(hr: int) -> bool:
    return ctypes.c_ulong(hr).value == S_OK


def _hr(value: int) -> int:
    return ctypes.c_ulong(value).value


def _dll_path() -> str:
    for path in DLL_CANDIDATES:
        if path.is_file():
            return str(path)
    raise FileNotFoundError("SimConnect.dll nicht gefunden")


def _bind(dll: ctypes.WinDLL) -> None:
    # restype must NOT be HRESULT: E_FAIL (keine Nachricht) wuerde sonst crashen.
    dll.SimConnect_Open.restype = ctypes.c_long
    dll.SimConnect_Open.argtypes = [POINTER(HANDLE), LPCSTR, HWND, DWORD, HANDLE, DWORD]
    dll.SimConnect_Close.restype = ctypes.c_long
    dll.SimConnect_Close.argtypes = [HANDLE]
    dll.SimConnect_AddToDataDefinition.restype = ctypes.c_long
    dll.SimConnect_AddToDataDefinition.argtypes = [
        HANDLE,
        DWORD,
        LPCSTR,
        LPCSTR,
        DWORD,
        ctypes.c_float,
        DWORD,
    ]
    dll.SimConnect_RequestDataOnSimObject.restype = ctypes.c_long
    dll.SimConnect_RequestDataOnSimObject.argtypes = [
        HANDLE,
        DWORD,
        DWORD,
        DWORD,
        DWORD,
        DWORD,
        DWORD,
        DWORD,
        DWORD,
    ]
    dll.SimConnect_AddToFacilityDefinition.restype = ctypes.c_long
    dll.SimConnect_AddToFacilityDefinition.argtypes = [HANDLE, DWORD, LPCSTR]
    dll.SimConnect_RequestFacilityData.restype = ctypes.c_long
    dll.SimConnect_RequestFacilityData.argtypes = [HANDLE, DWORD, DWORD, LPCSTR, LPCSTR]
    dll.SimConnect_WeatherRequestObservationAtStation.restype = ctypes.c_long
    dll.SimConnect_WeatherRequestObservationAtStation.argtypes = [HANDLE, DWORD, LPCSTR]
    dll.SimConnect_WeatherRequestObservationAtNearestStation.restype = ctypes.c_long
    dll.SimConnect_WeatherRequestObservationAtNearestStation.argtypes = [
        HANDLE,
        DWORD,
        ctypes.c_float,
        ctypes.c_float,
    ]
    dll.SimConnect_GetNextDispatch.restype = ctypes.c_long
    dll.SimConnect_GetNextDispatch.argtypes = [
        HANDLE,
        POINTER(POINTER(SIMCONNECT_RECV)),
        POINTER(DWORD),
    ]


class SimConnectBridge:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.connected = False
        self.ownship: dict | None = None
        self.last_error = ""
        self._stop = False
        self._gps = {"origin": "", "dest": ""}
        self.wx: dict = {}
        self.metar = ""
        self.atc_runway = ""
        self.atc_rwy_airport = ""
        self._metar_icao = ""
        self._last_metar_req = 0.0
        self.on_ownship = None
        self._fac_queue: queue.Queue[str] = queue.Queue()
        self._fac_pending: dict[int, str] = {}
        self._fac_asked: set[str] = set()
        self._fac_next = REQ_FAC_BASE
        self.airport_names: dict[str, str] = self._load_names()
        threading.Thread(target=self._loop, daemon=True, name="simconnect").start()

    @staticmethod
    def _load_names() -> dict[str, str]:
        try:
            raw = json.loads(data_file("msfs_names.json").read_text(encoding="utf-8"))
            return {str(k).upper(): str(v) for k, v in raw.items() if v}
        except Exception:
            return {}

    def _save_names(self) -> None:
        try:
            data_file("msfs_names.json", write=True).write_text(
                json.dumps(self.airport_names, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
        except OSError:
            pass

    def airport_name(self, icao: str) -> str:
        """MSFS facility name (cached); queues a lookup if unknown."""
        code = (icao or "").strip().upper()
        if len(code) < 3:
            return ""
        name = self.airport_names.get(code, "")
        if not name and code not in self._fac_asked:
            self._fac_asked.add(code)
            self._fac_queue.put(code)
        return name

    def _drain_facility_queue(self, dll, handle) -> None:
        while True:
            try:
                icao = self._fac_queue.get_nowait()
            except queue.Empty:
                return
            rid = self._fac_next
            self._fac_next += 1
            self._fac_pending[rid] = icao
            dll.SimConnect_RequestFacilityData(handle, DEF_FAC, rid, icao.encode("ascii", "ignore"), None)

    def _on_facility(self, pp) -> None:
        size = pp.contents.dwSize
        if size < FAC_HEADER + 64:
            return
        raw = ctypes.string_at(ctypes.addressof(pp.contents), size)
        rid = int.from_bytes(raw[12:16], "little")
        ftype = int.from_bytes(raw[24:28], "little")
        icao = self._fac_pending.get(rid)
        if not icao or ftype != 0:
            return
        name = _cstr(raw[FAC_HEADER:FAC_HEADER + 64])
        if name:
            self.airport_names[icao] = name
            self._save_names()

    def snapshot(self) -> tuple[dict | None, bool, str]:
        with self._lock:
            own = dict(self.ownship) if self.ownship else None
            return own, self.connected, self.last_error

    def weather_snapshot(self) -> dict:
        with self._lock:
            return {
                "wx": dict(self.wx),
                "metar": self.metar,
                "atcRunway": self.atc_runway,
                "atcRwyAirport": self.atc_rwy_airport,
            }

    def set_metar_icao(self, icao: str) -> None:
        code = (icao or "").strip().upper()
        with self._lock:
            self._metar_icao = code if len(code) == 4 else ""

    def _loop(self) -> None:
        while not self._stop:
            try:
                self._session()
            except Exception as exc:
                self.last_error = str(exc)
            with self._lock:
                self.connected = False
                self.ownship = None
                self.wx = {}
                self.metar = ""
                self.atc_runway = ""
            time.sleep(2.0)

    def _session(self) -> None:
        dll = ctypes.WinDLL(_dll_path())
        _bind(dll)

        handle = HANDLE()
        hr = dll.SimConnect_Open(byref(handle), b"BATCRadar", None, 0, None, 0)
        if not _ok(hr):
            self.last_error = f"MSFS nicht erreichbar ({_hr(hr)})"
            print(f"  {self.last_error}", flush=True)
            return

        opened = False
        deadline = time.monotonic() + 12.0
        pp = POINTER(SIMCONNECT_RECV)()
        cb = DWORD()
        while time.monotonic() < deadline and not self._stop:
            hr = dll.SimConnect_GetNextDispatch(handle, byref(pp), byref(cb))
            code = _hr(hr)
            if code == E_FAIL:
                time.sleep(0.05)
                continue
            if code != S_OK:
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
            self.last_error = "SimConnect OPEN Timeout"
            print("  SimConnect OPEN Timeout", flush=True)
            return

        print("  SimConnect OPEN", flush=True)

        unused = 0xFFFFFFFF
        dt_f64 = SIMCONNECT_DATATYPE.SIMCONNECT_DATATYPE_FLOAT64
        dt_s32 = SIMCONNECT_DATATYPE.SIMCONNECT_DATATYPE_STRING32
        dt_s256 = SIMCONNECT_DATATYPE.SIMCONNECT_DATATYPE_STRING256

        numeric = [
            (b"PLANE LATITUDE", b"Degrees"),
            (b"PLANE LONGITUDE", b"Degrees"),
            (b"PLANE ALTITUDE", b"Feet"),
            (b"GROUND VELOCITY", b"Knots"),
            (b"PLANE HEADING DEGREES TRUE", b"Degrees"),
            (b"SIM ON GROUND", b"Bool"),
            (b"BRAKE PARKING POSITION", b"Bool"),
        ]
        for name, unit in numeric:
            dll.SimConnect_AddToDataDefinition(handle, DEF_AC, name, unit, dt_f64, 0.0, unused)
            dll.SimConnect_AddToDataDefinition(handle, DEF_POSE, name, unit, dt_f64, 0.0, unused)
        for name in (b"ATC ID", b"ATC AIRLINE", b"ATC FLIGHT NUMBER", b"ATC MODEL"):
            dll.SimConnect_AddToDataDefinition(handle, DEF_AC, name, b"", dt_s32, 0.0, unused)
        dll.SimConnect_AddToDataDefinition(handle, DEF_AC, b"TITLE", b"", dt_s256, 0.0, unused)

        dll.SimConnect_AddToDataDefinition(handle, DEF_GPS, b"GPS WP PREV ID", b"", dt_s32, 0.0, unused)
        dll.SimConnect_AddToDataDefinition(handle, DEF_GPS, b"GPS WP NEXT ID", b"", dt_s32, 0.0, unused)

        for name, unit in (
            (b"AMBIENT WIND DIRECTION", b"Degrees"),
            (b"AMBIENT WIND VELOCITY", b"Knots"),
            (b"AMBIENT TEMPERATURE", b"Celsius"),
            (b"AMBIENT DEWPOINT", b"Celsius"),
            (b"SEA LEVEL PRESSURE", b"Millibars"),
            (b"AMBIENT VISIBILITY", b"Meters"),
        ):
            dll.SimConnect_AddToDataDefinition(handle, DEF_WX, name, unit, dt_f64, 0.0, unused)
        dll.SimConnect_AddToDataDefinition(handle, DEF_RWY, b"ATC RUNWAY SELECTED", b"", dt_s32, 0.0, unused)
        dll.SimConnect_AddToDataDefinition(handle, DEF_RWY, b"ATC RUNWAY AIRPORT NAME", b"", dt_s32, 0.0, unused)

        for cmd in (b"OPEN AIRPORT", b"NAME64", b"CLOSE AIRPORT"):
            dll.SimConnect_AddToFacilityDefinition(handle, DEF_FAC, cmd)
        self._fac_asked = set(self.airport_names)

        def _request(req_id: int, def_id: int, period, interval: int) -> None:
            hr = dll.SimConnect_RequestDataOnSimObject(
                handle,
                req_id,
                def_id,
                0,
                int(period),
                0,
                0,
                interval,
                0,
            )
            if not _ok(hr):
                print(f"  SimConnect request {req_id} fehlgeschlagen ({_hr(hr)})", flush=True)

        _request(REQ_POSE, DEF_POSE, SIMCONNECT_PERIOD.SIMCONNECT_PERIOD_VISUAL_FRAME, 1)
        _request(REQ_USER, DEF_AC, SIMCONNECT_PERIOD.SIMCONNECT_PERIOD_SECOND, 0)
        _request(REQ_GPS, DEF_GPS, SIMCONNECT_PERIOD.SIMCONNECT_PERIOD_SECOND, 0)
        _request(REQ_WX, DEF_WX, SIMCONNECT_PERIOD.SIMCONNECT_PERIOD_SECOND, 0)
        _request(REQ_RWY, DEF_RWY, SIMCONNECT_PERIOD.SIMCONNECT_PERIOD_SECOND, 0)

        print("  SimConnect verbunden.", flush=True)

        with self._lock:
            self.connected = True
            self.last_error = ""

        try:
            while not self._stop:
                self._drain_facility_queue(dll, handle)
                self._poll_metar(dll, handle)
                hr = dll.SimConnect_GetNextDispatch(handle, byref(pp), byref(cb))
                code = _hr(hr)
                if code == E_FAIL:
                    time.sleep(0.001)
                    continue
                if code != S_OK:
                    time.sleep(0.02)
                    continue

                recv_id = pp.contents.dwID
                if recv_id == SIMCONNECT_RECV_ID.SIMCONNECT_RECV_ID_QUIT:
                    return
                if recv_id == RECV_FACILITY_DATA:
                    self._on_facility(pp)
                    continue
                if recv_id == RECV_WEATHER_OBS:
                    self._on_metar(pp)
                    continue
                if recv_id == SIMCONNECT_RECV_ID.SIMCONNECT_RECV_ID_EXCEPTION:
                    if not getattr(self, "_exc_logged", False):
                        self._exc_logged = True
                        exc = ctypes.cast(pp, POINTER(SIMCONNECT_RECV_EXCEPTION)).contents
                        print(f"  SimConnect exception {int(exc.dwException)}", flush=True)
                    continue
                if recv_id == SIMCONNECT_RECV_ID.SIMCONNECT_RECV_ID_SIMOBJECT_DATA:
                    self._on_object(ctypes.cast(pp, POINTER(SIMCONNECT_RECV_SIMOBJECT_DATA)).contents)
        finally:
            try:
                dll.SimConnect_Close(handle)
            except OSError:
                pass

    def _poll_metar(self, dll, handle) -> None:
        now = time.monotonic()
        if now - self._last_metar_req < 20.0:
            return
        self._last_metar_req = now
        own = self.ownship
        if own:
            dll.SimConnect_WeatherRequestObservationAtNearestStation(
                handle,
                REQ_METAR_NEAR,
                ctypes.c_float(own["lat"]),
                ctypes.c_float(own["lon"]),
            )
        icao = self._metar_icao
        if icao:
            dll.SimConnect_WeatherRequestObservationAtStation(
                handle, REQ_METAR_STN, icao.encode("ascii", "ignore")
            )

    def _on_metar(self, pp) -> None:
        size = pp.contents.dwSize
        if size < 16:
            return
        raw = ctypes.string_at(ctypes.addressof(pp.contents), size)
        text = _cstr(raw[16:])
        if not text:
            return
        with self._lock:
            self.metar = text.strip()

    def _on_object(self, msg: SIMCONNECT_RECV_SIMOBJECT_DATA) -> None:
        req = msg.dwRequestID
        if req == REQ_WX:
            blob = WxBlob.from_buffer_copy(_blob_bytes(msg, sizeof(WxBlob)))
            with self._lock:
                self.wx = {
                    "windFrom": float(blob.wind_from) % 360.0,
                    "windKt": float(blob.wind_kt),
                    "tempC": float(blob.temp_c),
                    "dewC": float(blob.dew_c),
                    "qnh": float(blob.qnh),
                    "visM": float(blob.vis_m),
                }
            return
        if req == REQ_RWY:
            blob = RwyBlob.from_buffer_copy(_blob_bytes(msg, sizeof(RwyBlob)))
            with self._lock:
                self.atc_runway = _cstr(blob.selected)
                self.atc_rwy_airport = _cstr(blob.airport)
            return
        if req == REQ_GPS:
            blob = GpsBlob.from_buffer_copy(_blob_bytes(msg, sizeof(GpsBlob)))
            with self._lock:
                self._gps["origin"] = _cstr(blob.prev_id)
                self._gps["dest"] = _cstr(blob.next_id)
            return

        if req == REQ_POSE:
            pose = PoseBlob.from_buffer_copy(_blob_bytes(msg, sizeof(PoseBlob)))
            if abs(pose.lat) < 0.001 and abs(pose.lon) < 0.001:
                return
            with self._lock:
                plane = dict(self.ownship) if self.ownship else {
                    "object_id": int(msg.dwObjectID),
                    "atc_id": "",
                    "airline": "",
                    "flight_number": "",
                    "model": "",
                    "title": "",
                    "gps_origin": self._gps["origin"],
                    "gps_dest": self._gps["dest"],
                }
                plane["object_id"] = int(msg.dwObjectID)
                plane["lat"] = float(pose.lat)
                plane["lon"] = float(pose.lon)
                plane["alt"] = float(pose.alt)
                plane["gs"] = float(pose.gs)
                plane["hdg"] = float(pose.hdg) % 360.0
                plane["simOnGround"] = float(pose.on_ground) > 0.5
                plane["parkingBrake"] = float(pose.park_brake) > 0.5
                self.ownship = plane
            cb = self.on_ownship
            if cb:
                try:
                    cb(plane)
                except Exception:
                    pass
            return

        if req != REQ_USER:
            return

        raw = _blob_bytes(msg, sizeof(AircraftBlob))
        if len(raw) < sizeof(AircraftBlob):
            return
        blob = AircraftBlob.from_buffer_copy(raw)
        if abs(blob.lat) < 0.001 and abs(blob.lon) < 0.001:
            return

        plane = {
            "object_id": int(msg.dwObjectID),
            "lat": float(blob.lat),
            "lon": float(blob.lon),
            "alt": float(blob.alt),
            "gs": float(blob.gs),
            "hdg": float(blob.hdg) % 360.0,
            "simOnGround": float(blob.on_ground) > 0.5,
            "parkingBrake": float(blob.park_brake) > 0.5,
            "atc_id": _cstr(blob.atc_id),
            "airline": _cstr(blob.airline),
            "flight_number": _cstr(blob.flight_number),
            "model": _cstr(blob.model),
            "title": _cstr(blob.title),
            "gps_origin": self._gps["origin"],
            "gps_dest": self._gps["dest"],
        }
        with self._lock:
            self.ownship = plane
        cb = self.on_ownship
        if cb:
            try:
                cb(plane)
            except Exception:
                pass
