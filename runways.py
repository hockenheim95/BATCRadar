"""Public runway thresholds (OurAirports), cached after first download."""

from __future__ import annotations

import csv
import io
import json
import threading
import urllib.request

from paths import data_file

CACHE = data_file("runways.json")
SOURCE = "https://davidmegginson.github.io/ourairports-data/runways.csv"

_lock = threading.Lock()
_by_airport: dict[str, dict[str, tuple[float, float, float]]] | None = None


def _norm_rwy(text: str) -> str:
    raw = (text or "").strip().upper().replace("RWY", "").replace("RUNWAY", "").strip()
    if raw.startswith("0") and len(raw) >= 2 and raw[1].isdigit():
        stripped = raw.lstrip("0")
        return stripped or "0"
    return raw


def _load_csv(blob: str) -> dict[str, dict[str, tuple[float, float, float]]]:
    out: dict[str, dict[str, tuple[float, float, float]]] = {}
    reader = csv.DictReader(io.StringIO(blob))
    for row in reader:
        icao = (row.get("airport_ident") or "").strip().upper()
        if len(icao) != 4:
            continue
        bucket = out.setdefault(icao, {})
        for prefix in ("le", "he"):
                    ident = (row.get(f"{prefix}_ident") or "").strip().upper()
                    try:
                        lat = float(row.get(f"{prefix}_latitude_deg") or "")
                        lon = float(row.get(f"{prefix}_longitude_deg") or "")
                        hdg = float(row.get(f"{prefix}_heading_degT") or "")
                    except ValueError:
                        continue
                    if not ident or abs(lat) < 0.01:
                        continue
                    pt = (round(lat, 5), round(lon, 5), round(hdg % 360.0, 1))
                    bucket[ident] = pt
                    bucket[_norm_rwy(ident)] = pt
    return out


def _ensure() -> dict[str, dict[str, tuple[float, float, float]]]:
    global _by_airport
    with _lock:
        if _by_airport is not None:
            return _by_airport
        if CACHE.is_file():
            raw = json.loads(CACHE.read_text(encoding="utf-8"))
            _by_airport = {
                icao: {ident: (a, b, c) for ident, (a, b, c) in rwys.items()}
                for icao, rwys in raw.items()
            }
            return _by_airport
        print("  Runways: lade OurAirports (einmalig)…", flush=True)
        with urllib.request.urlopen(SOURCE, timeout=40) as resp:
            blob = resp.read().decode("utf-8", "replace")
        parsed = _load_csv(blob)
        data_file("runways.json", write=True).write_text(
            json.dumps(parsed, separators=(",", ":")), encoding="utf-8"
        )
        _by_airport = parsed
        print(f"  Runways: {len(parsed)} Airports gecacht.", flush=True)
        return _by_airport


def runway_end(icao: str, ident: str, hdg: float | None = None, lat: float | None = None, lon: float | None = None) -> tuple[float, float, float] | None:
    try:
        from navdata import runway_end as ng_end

        hit = ng_end(icao, ident, hdg, lat, lon)
        if hit:
            return hit
    except Exception:
        pass
    if not icao or not ident:
        return None
    data = _ensure()
    rwys = data.get(icao.strip().upper())
    if not rwys:
        return None
    key = _norm_rwy(ident)
    hit = rwys.get(key)
    if hit:
        return hit
    padded = key.zfill(2) if key[:1].isdigit() and len(key) < 3 else key
    return rwys.get(_norm_rwy(padded))


def warmup() -> None:
    threading.Thread(target=_ensure, daemon=True, name="runways").start()
