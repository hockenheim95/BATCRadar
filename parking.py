"""Match aircraft lat/lon to scenery / GSX parking names."""

from __future__ import annotations

import math
import re
import threading
from pathlib import Path

MAX_M = 50.0
GSX_DIR = Path.home() / "AppData" / "Roaming" / "Virtuali" / "GSX" / "MSFS"
PACKAGES = Path.home() / "AppData" / "Roaming" / "Microsoft Flight Simulator 2024" / "Packages"
PKG_SUBS = ("Community", "Community2024", "Official2024", "Official2020")

_NAME_LETTER = {
    "GATE": "G",
    "DOCK": "D",
    "PARKING": "",
    "N_PARKING": "N",
    "NE_PARKING": "NE",
    "E_PARKING": "E",
    "SE_PARKING": "SE",
    "S_PARKING": "S",
    "SW_PARKING": "SW",
    "W_PARKING": "W",
    "NW_PARKING": "NW",
    "NONE": "",
}

_lock = threading.Lock()
_cache: dict[str, list[tuple[float, float, str]]] = {}
_gsx_index: dict[str, list[Path]] | None = None
_origin_gate: dict[str, str] = {}

_TAG = re.compile(r"<TaxiwayParking\b([^>]*)/?>", re.I)
_ATTR = re.compile(r'([A-Za-z_]+)\s*=\s*"([^"]*)"')
_SECTION = re.compile(r"^\[([^\]]+)\]\s*$")
_ICAO_FILE = re.compile(r"^([A-Z]{4})", re.I)


def _m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    d1 = math.radians(lat2 - lat1)
    d2 = math.radians(lon2 - lon1)
    a = math.sin(d1 / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(d2 / 2) ** 2
    return 6371000.0 * 2 * math.asin(min(1.0, math.sqrt(a)))


def _strip_gate_noise(label: str) -> str:
    s = re.sub(r"\s+", "", (label or "").strip()).upper()
    return re.sub(r"PARKING|GATE|STAND|DOCK", "", s)


def _label_from_section(section: str) -> str:
    raw = (section or "").strip()
    m = re.match(r"(?:gate|parking|dock|stand)\s+(.+)$", raw, re.I)
    rest = m.group(1) if m else raw
    return _strip_gate_noise(rest)


def _label_from_xml(name: str, number: str, suffix: str) -> str:
    key = (name or "").strip().upper()
    letter = _NAME_LETTER.get(key)
    if letter is None and key.startswith("GATE_") and len(key) == 6:
        letter = key[-1]
    elif letter is None:
        letter = re.sub(r"[^A-Z0-9]", "", key.replace("GATE", "").replace("PARKING", ""))
    num = (number or "").strip()
    if num.endswith(".0"):
        num = num[:-2]
    if num.endswith(".00"):
        num = num[:-3]
    if num.isdigit():
        num = str(int(num))
    suf = (suffix or "").strip().upper()
    if suf in ("NONE", "0"):
        suf = ""
    return _strip_gate_noise(f"{letter}{num}{suf}")


def _gsx_files() -> dict[str, list[Path]]:
    global _gsx_index
    if _gsx_index is not None:
        return _gsx_index
    idx: dict[str, list[Path]] = {}
    if GSX_DIR.is_dir():
        for path in GSX_DIR.glob("*.ini"):
            m = _ICAO_FILE.match(path.name)
            if not m:
                continue
            icao = m.group(1).upper()
            idx.setdefault(icao, []).append(path)
    _gsx_index = idx
    return idx


def _load_gsx(icao: str) -> list[tuple[float, float, str]]:
    out: list[tuple[float, float, str]] = []
    for path in _gsx_files().get(icao, []):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        section = ""
        lat = lon = None
        stop_lat = stop_lon = None
        for line in text.splitlines():
            sm = _SECTION.match(line.strip())
            if sm:
                if section and lat is None and stop_lat is not None:
                    lat, lon = stop_lat, stop_lon
                if section and lat is not None and lon is not None:
                    out.append((lat, lon, _label_from_section(section)))
                section = sm.group(1)
                lat = lon = stop_lat = stop_lon = None
                continue
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip().lower()
            parts = val.strip().split()
            if key == "this_parking_pos" and len(parts) >= 2:
                try:
                    lat, lon = float(parts[0]), float(parts[1])
                except ValueError:
                    pass
            elif key == "parkingsystem_stopposition" and len(parts) >= 2:
                try:
                    stop_lat, stop_lon = float(parts[0]), float(parts[1])
                except ValueError:
                    pass
        if section and lat is None and stop_lat is not None:
            lat, lon = stop_lat, stop_lon
        if section and lat is not None and lon is not None:
            out.append((lat, lon, _label_from_section(section)))
    return out


def _load_xml(icao: str) -> list[tuple[float, float, str]]:
    needle = icao.lower()
    out: list[tuple[float, float, str]] = []
    if not PACKAGES.is_dir():
        return out
    for sub in PKG_SUBS:
        root = PACKAGES / sub
        if not root.is_dir():
            continue
        for pkg in root.iterdir():
            if not pkg.is_dir() or needle not in pkg.name.lower():
                continue
            for xml in pkg.rglob("*.xml"):
                try:
                    if xml.stat().st_size > 8_000_000:
                        continue
                    text = xml.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                if "TaxiwayParking" not in text:
                    continue
                for tag in _TAG.finditer(text):
                    attrs = {k.lower(): v for k, v in _ATTR.findall(tag.group(1))}
                    try:
                        lat = float(attrs.get("lat") or "")
                        lon = float(attrs.get("lon") or "")
                    except ValueError:
                        continue
                    name = _label_from_xml(
                        attrs.get("name") or "",
                        attrs.get("number") or "",
                        attrs.get("suffix") or "",
                    )
                    if name:
                        out.append((lat, lon, name))
    return out


def _load_sim(icao: str) -> list[tuple[float, float, str]] | None:
    """Same parking source GSX uses without a custom INI (SimConnect TAXI_PARKING).

    None = sim data not ready yet (do not cache). Empty list = no usable stands.
    """
    import msfs_procs

    if msfs_procs.status(icao) != "ready":
        return None
    data = msfs_procs.airport(icao) or {}
    out: list[tuple[float, float, str]] = []
    for row in data.get("parkings") or []:
        name = _strip_gate_noise(str(row.get("name") or ""))
        if not name:
            continue
        try:
            out.append((float(row["lat"]), float(row["lon"]), name))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def parkings(icao: str) -> list[tuple[float, float, str]]:
    code = (icao or "").strip().upper()
    if len(code) != 4:
        return []
    with _lock:
        if code in _cache:
            return _cache[code]
    spots = _load_gsx(code)
    if not spots:
        sim = _load_sim(code)
        if sim is None:
            return []
        spots = sim
    if not spots:
        spots = _load_xml(code)
    with _lock:
        _cache[code] = spots
    return spots


def nearest_gate(icao: str, lat: float, lon: float, max_m: float = MAX_M) -> str:
    best = ""
    best_d = max_m
    for slat, slon, name in parkings(icao):
        d = _m(lat, lon, slat, slon)
        if d < best_d:
            best_d = d
            best = name
    return best


def attach_gate(plane: dict) -> None:
    pid = str(plane.get("id") or "")
    if not plane.get("onGround"):
        plane["gate"] = ""
        if pid:
            _origin_gate.pop(pid, None)
        return
    icao = ""
    if plane.get("atDestination"):
        icao = plane.get("dest") or ""
    if not icao:
        icao = plane.get("origin") or plane.get("dest") or ""
    gate = nearest_gate(icao, float(plane.get("lat") or 0), float(plane.get("lon") or 0))
    if plane.get("atDestination"):
        plane["gate"] = gate
        return
    if gate and pid:
        _origin_gate[pid] = gate
    elif pid and pid in _origin_gate:
        gate = _origin_gate[pid]
    plane["gate"] = gate


def warmup() -> None:
    threading.Thread(target=_gsx_files, daemon=True, name="gsx-park").start()
