"""Cached current weather (Open-Meteo) and METAR."""

from __future__ import annotations

import calendar
import json
import math
import re
import threading
import time
import urllib.parse
import urllib.request

from airports import nearest as nearest_airport
from airports import nearest_cities

WX_TTL = 12 * 60
METAR_TTL = 8 * 60

# Open-Meteo pressure levels (hPa), denser around typical cruise.
_LEVELS = (1000, 925, 850, 700, 500, 400, 300, 250, 200, 150)

_lock = threading.Lock()
_wx: dict[tuple[float, float], tuple[float, dict]] = {}
_aloft: dict[tuple[float, float], tuple[float, dict]] = {}
_metar: dict[str, tuple[float, str]] = {}
_metar_busy: set[str] = set()


def _get(url: str, timeout: float = 8.0) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "BATCRadar/1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _isa_hpa(alt_ft: float) -> float:
    h = max(-2000.0, float(alt_ft))
    if h <= 36089.24:
        return 1013.25 * (1.0 - h / 145442.16) ** 5.255876
    return 226.32 * math.exp(-0.000157688 * (h - 36089.24))


def _hour_index(times: list) -> int:
    if not times:
        return 0
    now = time.time()
    best_i = 0
    best_dt = 1e18
    for i, stamp in enumerate(times):
        try:
            t = time.strptime(str(stamp)[:16], "%Y-%m-%dT%H:%M")
            dt = abs(calendar.timegm(t) - now)
        except (ValueError, OverflowError, OSError):
            continue
        if dt < best_dt:
            best_dt = dt
            best_i = i
    return best_i


def _hourly_val(hourly: dict, name: str, idx: int):
    series = hourly.get(name) or []
    if idx < 0 or idx >= len(series):
        return None
    val = series[idx]
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _interp(hpa: float, levels: list[float], values: list):
    pts = [(p, v) for p, v in zip(levels, values) if v is not None]
    if not pts:
        return None
    if hpa >= pts[0][0]:
        return pts[0][1]
    if hpa <= pts[-1][0]:
        return pts[-1][1]
    for i in range(len(pts) - 1):
        p0, v0 = pts[i]
        p1, v1 = pts[i + 1]
        if p1 <= hpa <= p0:
            span = math.log(p1) - math.log(p0)
            if abs(span) < 1e-9:
                return v0
            w = (math.log(hpa) - math.log(p0)) / span
            return v0 + w * (v1 - v0)
    return pts[-1][1]


def _wind_uv(kt, deg):
    if kt is None or deg is None:
        return None, None
    rad = math.radians(float(deg) % 360.0)
    return -float(kt) * math.sin(rad), -float(kt) * math.cos(rad)


def _from_uv(u, v):
    if u is None or v is None:
        return None, None
    kt = math.hypot(u, v)
    deg = (math.degrees(math.atan2(-u, -v)) + 360.0) % 360.0
    return kt, deg


def _aloft_hourly_names() -> str:
    bits = []
    for p in _LEVELS:
        bits.append(f"temperature_{p}hPa")
        bits.append(f"wind_speed_{p}hPa")
        bits.append(f"wind_direction_{p}hPa")
    return ",".join(bits)


def _fetch_aloft(lat: float, lon: float) -> dict:
    url = (
        "https://api.open-meteo.com/v1/forecast?"
        + urllib.parse.urlencode(
            {
                "latitude": f"{lat:.2f}",
                "longitude": f"{lon:.2f}",
                "current": "temperature_2m,weather_code,wind_speed_10m,wind_direction_10m",
                "hourly": _aloft_hourly_names(),
                "wind_speed_unit": "kn",
                "forecast_days": "1",
                "timezone": "UTC",
            }
        )
    )
    data = _get(url)
    cur = data.get("current") or {}
    hourly = data.get("hourly") or {}
    idx = _hour_index(hourly.get("time") or [])
    temps, speeds, dirs = [], [], []
    for p in _LEVELS:
        temps.append(_hourly_val(hourly, f"temperature_{p}hPa", idx))
        speeds.append(_hourly_val(hourly, f"wind_speed_{p}hPa", idx))
        dirs.append(_hourly_val(hourly, f"wind_direction_{p}hPa", idx))
    return {
        "code": cur.get("weather_code"),
        "sfcTempC": cur.get("temperature_2m"),
        "sfcWindKt": cur.get("wind_speed_10m"),
        "sfcWindFrom": cur.get("wind_direction_10m"),
        "temps": temps,
        "speeds": speeds,
        "dirs": dirs,
    }


def _profile(lat: float, lon: float) -> dict:
    key = (round(float(lat), 2), round(float(lon), 2))
    now = time.monotonic()
    with _lock:
        hit = _aloft.get(key)
        if hit and now - hit[0] < WX_TTL:
            return hit[1]
    try:
        pack = _fetch_aloft(key[0], key[1])
    except Exception:
        pack = {
            "code": None,
            "sfcTempC": None,
            "sfcWindKt": None,
            "sfcWindFrom": None,
            "temps": [None] * len(_LEVELS),
            "speeds": [None] * len(_LEVELS),
            "dirs": [None] * len(_LEVELS),
        }
    with _lock:
        _aloft[key] = (now, pack)
    return pack


def _qnh_from_metar(text: str):
    blob = (text or "").upper()
    q = re.search(r"\bQ(\d{4})\b", blob)
    if q:
        return float(q.group(1))
    a = re.search(r"\bA(\d{4})\b", blob)
    if a:
        return round(float(a.group(1)) * 0.3386, 1)
    return None


def _nearest_qnh(lat: float, lon: float, wait: bool = False):
    apt = nearest_airport(lat, lon, max_nm=250.0)
    if not apt:
        return None
    icao = apt[0]
    return _qnh_from_metar(metar_raw(icao, wait=wait))


def current(lat: float, lon: float, alt_ft: float | None = None) -> dict:
    if alt_ft is None or not math.isfinite(float(alt_ft)):
        return _surface(lat, lon)
    pack = _profile(lat, lon)
    hpa = _isa_hpa(float(alt_ft))
    levels = [float(p) for p in _LEVELS]
    temp = _interp(hpa, levels, pack["temps"])
    us, vs = [], []
    for kt, deg in zip(pack["speeds"], pack["dirs"]):
        u, v = _wind_uv(kt, deg)
        us.append(u)
        vs.append(v)
    u = _interp(hpa, levels, us)
    v = _interp(hpa, levels, vs)
    wind_kt, wind_from = _from_uv(u, v)
    if temp is None:
        temp = pack.get("sfcTempC")
    if wind_kt is None:
        wind_kt = pack.get("sfcWindKt")
        wind_from = pack.get("sfcWindFrom")
    return {
        "tempC": None if temp is None else round(float(temp), 1),
        "code": pack.get("code"),
        "windKt": None if wind_kt is None else round(float(wind_kt), 1),
        "windFrom": None if wind_from is None else round(float(wind_from) % 360.0, 0),
        "qnh": _nearest_qnh(lat, lon, wait=False),
        "altFt": round(float(alt_ft)),
    }


def enroute(lat: float, lon: float) -> dict:
    """Surface weather plus nearest cities for the en-route panel."""
    try:
        out = dict(_surface(lat, lon))
    except Exception:
        out = {"tempC": None, "code": None, "windKt": None, "windFrom": None, "qnh": None}
    try:
        out["qnh"] = _nearest_qnh(lat, lon, wait=False)
    except Exception:
        out.setdefault("qnh", None)
    try:
        out["cities"] = nearest_cities(lat, lon, count=6, max_km=400.0)
    except Exception:
        out["cities"] = []
    return out


def _surface(lat: float, lon: float) -> dict:
    key = (round(float(lat), 2), round(float(lon), 2))
    now = time.monotonic()
    with _lock:
        hit = _wx.get(key)
        if hit and now - hit[0] < WX_TTL:
            return hit[1]
    url = (
        "https://api.open-meteo.com/v1/forecast?"
        + urllib.parse.urlencode(
            {
                "latitude": f"{key[0]:.2f}",
                "longitude": f"{key[1]:.2f}",
                "current": "temperature_2m,weather_code,wind_speed_10m,wind_direction_10m",
                "wind_speed_unit": "kn",
            }
        )
    )
    try:
        data = _get(url)
        cur = data.get("current") or {}
        out = {
            "tempC": cur.get("temperature_2m"),
            "code": cur.get("weather_code"),
            "windKt": cur.get("wind_speed_10m"),
            "windFrom": cur.get("wind_direction_10m"),
            "qnh": None,
        }
    except Exception:
        out = {"tempC": None, "code": None, "windKt": None, "windFrom": None, "qnh": None}
    with _lock:
        _wx[key] = (now, out)
    return out


def metar_raw(icao: str, wait: bool = False) -> str:
    code = (icao or "").strip().upper()
    if len(code) != 4:
        return ""
    now = time.monotonic()
    with _lock:
        hit = _metar.get(code)
        if hit and now - hit[0] < METAR_TTL:
            return hit[1]
        stale = hit[1] if hit else ""
    if not wait:
        with _lock:
            if code in _metar_busy:
                return stale
            _metar_busy.add(code)
        threading.Thread(target=_fetch_metar, args=(code,), daemon=True, name="metar").start()
        return stale
    return _fetch_metar(code)


def _fetch_metar(code: str) -> str:
    url = "https://aviationweather.gov/api/data/metar?" + urllib.parse.urlencode(
        {"ids": code, "format": "raw", "hours": "2"}
    )
    text = ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "BATCRadar/1"})
        with urllib.request.urlopen(req, timeout=6.0) as resp:
            text = resp.read().decode("utf-8", "replace").strip().splitlines()[0].strip()
    except Exception:
        text = ""
    with _lock:
        _metar[code] = (time.monotonic(), text)
        _metar_busy.discard(code)
    return text


