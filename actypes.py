"""ICAO aircraft type designators -> display names (DOC 8643 / OpenSky)."""

from __future__ import annotations

import csv
import io
import json
import threading
import urllib.request
from pathlib import Path

CACHE = Path(__file__).resolve().parent / "data" / "actypes.json"
SRC = "https://opensky-network.org/datasets/metadata/doc8643AircraftTypes.csv"

PREFERRED = {
    "A388": "Airbus A380-800",
    "A359": "Airbus A350-900",
    "A35K": "Airbus A350-1000",
    "A339": "Airbus A330-900neo",
    "A333": "Airbus A330-300",
    "A332": "Airbus A330-200",
    "A321": "Airbus A321",
    "A21N": "Airbus A321neo",
    "A320": "Airbus A320",
    "A20N": "Airbus A320neo",
    "A319": "Airbus A319",
    "A19N": "Airbus A319neo",
    "A318": "Airbus A318",
    "B748": "Boeing 747-8",
    "B744": "Boeing 747-400",
    "B77W": "Boeing 777-300ER",
    "B77L": "Boeing 777-200LR",
    "B772": "Boeing 777-200",
    "B773": "Boeing 777-300",
    "B789": "Boeing 787-9",
    "B788": "Boeing 787-8",
    "B78X": "Boeing 787-10",
    "B739": "Boeing 737-900",
    "B738": "Boeing 737-800",
    "B737": "Boeing 737-700",
    "B38M": "Boeing 737 MAX 8",
    "B39M": "Boeing 737 MAX 9",
    "B37M": "Boeing 737 MAX 7",
    "B3XM": "Boeing 737 MAX 10",
    "E190": "Embraer E190",
    "E195": "Embraer E195",
    "E290": "Embraer E190-E2",
    "E295": "Embraer E195-E2",
    "CRJ9": "Bombardier CRJ-900",
    "CRJ7": "Bombardier CRJ-700",
    "CRJ2": "Bombardier CRJ-200",
    "A343": "Airbus A340-300",
    "A346": "Airbus A340-600",
    "A310": "Airbus A310",
    "BCS1": "Airbus A220-100",
    "BCS3": "Airbus A220-300",
    "AT76": "ATR 72-600",
    "AT75": "ATR 72-500",
    "AT72": "ATR 72",
    "AT45": "ATR 42-500",
    "DH8D": "De Havilland Dash 8-400",
    "C56X": "Cessna Citation XLS",
    "GL5T": "Bombardier Global 5000",
    "C172": "Cessna 172",
    "C208": "Cessna 208 Caravan",
}

_lock = threading.Lock()
_map: dict[str, str] = {}
_ready = False


def _apply(mapping: dict[str, str]) -> None:
    global _map, _ready
    _map = mapping
    _ready = True


def _pretty(manufacturer: str, model: str) -> str:
    model = " ".join((model or "").split())
    manu = " ".join((manufacturer or "").split())
    if not model:
        return ""
    if manu and manu.lower() not in model.lower():
        if manu.isupper():
            manu = manu.title()
        return f"{manu} {model}"
    return model


def _score(name: str) -> tuple[int, int]:
    return (len(name), name.count(" "))


def _parse(blob: str) -> dict[str, str]:
    out: dict[str, str] = {}
    reader = csv.DictReader(io.StringIO(blob))
    for row in reader:
        designator = (row.get("Designator") or "").strip().upper()
        if len(designator) < 2 or len(designator) > 4 or not designator.isalnum():
            continue
        name = _pretty(row.get("ManufacturerCode") or "", row.get("ModelFullName") or "")
        if not name:
            continue
        prev = out.get(designator)
        if prev is None or _score(name) < _score(prev):
            out[designator] = name
    out.update(PREFERRED)
    return out


def _download() -> None:
    try:
        with urllib.request.urlopen(SRC, timeout=40) as resp:
            blob = resp.read().decode("utf-8", "replace")
        parsed = _parse(blob)
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(parsed, separators=(",", ":")), encoding="utf-8")
        with _lock:
            _apply(parsed)
        print(f"  Flugzeugtypen: {len(parsed)} Namen gecacht.", flush=True)
    except Exception as exc:
        print(f"  Flugzeugtypen: Download fehlgeschlagen ({exc})", flush=True)


def _ensure() -> dict[str, str]:
    global _ready
    with _lock:
        if _ready:
            return _map
        mapping = dict(PREFERRED)
        if CACHE.is_file():
            try:
                raw = json.loads(CACHE.read_text(encoding="utf-8"))
                mapping.update({str(k).upper(): str(v) for k, v in raw.items() if k and v})
            except Exception:
                pass
        mapping.update(PREFERRED)
        _apply(mapping)
    threading.Thread(target=_download, daemon=True, name="actypes").start()
    return _map


def name_of(icao_type: str) -> str:
    code = (icao_type or "").strip().upper()
    if not code:
        return ""
    return _ensure().get(code, "")


def warmup() -> None:
    threading.Thread(target=_ensure, daemon=True, name="actypes").start()
