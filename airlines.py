"""Airline ICAO -> IATA and names from OpenFlights, cached locally."""

from __future__ import annotations

import csv
import json
import threading
import urllib.request

from paths import data_file

CACHE = data_file("airlines.json")
NAME_CACHE = data_file("airline_names.json")
SRC = "https://raw.githubusercontent.com/jpatokal/openflights/master/data/airlines.dat"

_lock = threading.Lock()
_map: dict[str, str] = {}
_names: dict[str, str] = {}
_names_iata: dict[str, str] = {}
_ready = False


def _apply(
    mapping: dict[str, str],
    names: dict[str, str] | None = None,
    names_iata: dict[str, str] | None = None,
) -> None:
    global _map, _names, _names_iata, _ready
    _map = mapping
    if names is not None:
        _names = names
    if names_iata is not None:
        _names_iata = names_iata
    _ready = True


def _clean_name(raw: str) -> str:
    text = (raw or "").strip()
    if text in ("", "\\N", "N/A", "-"):
        return ""
    return text


def _parse(blob: str) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    iata_out: dict[str, str] = {}
    names: dict[str, str] = {}
    names_iata: dict[str, str] = {}
    for row in csv.reader(blob.splitlines()):
        if len(row) < 8:
            continue
        name = _clean_name(row[1]) or _clean_name(row[2])
        iata = (row[3] or "").strip().upper()
        icao = (row[4] or "").strip().upper()
        active = (row[7] or "").strip().upper()
        if icao in ("", "\\N", "N/A") or len(icao) != 3 or not icao.isalnum():
            icao = ""
        if iata in ("", "\\N", "-", "N/A") or len(iata) > 3:
            iata = ""
        if icao and name and (icao not in names or active == "Y"):
            names[icao] = name
        if iata and name and (iata not in names_iata or active == "Y"):
            names_iata[iata] = name
        if not icao or not iata:
            continue
        if icao in iata_out and active != "Y":
            continue
        iata_out[icao] = iata
    return iata_out, names, names_iata


def _write_caches(
    iata_map: dict[str, str], names: dict[str, str], names_iata: dict[str, str]
) -> None:
    data_file("airlines.json", write=True).write_text(
        json.dumps(iata_map, separators=(",", ":")), encoding="utf-8"
    )
    data_file("airline_names.json", write=True).write_text(
        json.dumps({"icao": names, "iata": names_iata}, separators=(",", ":")),
        encoding="utf-8",
    )


def _download() -> None:
    try:
        with urllib.request.urlopen(SRC, timeout=40) as resp:
            blob = resp.read().decode("utf-8", "replace")
        iata_map, names, names_iata = _parse(blob)
        _write_caches(iata_map, names, names_iata)
        with _lock:
            _apply(iata_map, names, names_iata)
        print(
            f"  Airlines: {len(iata_map)} IATA-Codes, {len(names)} Namen gecacht.",
            flush=True,
        )
    except Exception as exc:
        print(f"  Airlines: Download fehlgeschlagen ({exc})", flush=True)


def _load_names() -> tuple[dict[str, str], dict[str, str]]:
    if not NAME_CACHE.is_file():
        return {}, {}
    try:
        raw = json.loads(NAME_CACHE.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and "icao" in raw:
            icao = {str(k).upper(): str(v) for k, v in (raw.get("icao") or {}).items() if k and v}
            iata = {str(k).upper(): str(v) for k, v in (raw.get("iata") or {}).items() if k and v}
            return icao, iata
        if isinstance(raw, dict):
            return {str(k).upper(): str(v) for k, v in raw.items() if k and v}, {}
    except Exception:
        pass
    return {}, {}


def _ensure() -> dict[str, str]:
    global _ready
    with _lock:
        if _ready:
            return _map
        iata_map: dict[str, str] = {}
        if CACHE.is_file():
            try:
                raw = json.loads(CACHE.read_text(encoding="utf-8"))
                iata_map = {str(k).upper(): str(v).upper() for k, v in raw.items() if k and v}
            except Exception:
                iata_map = {}
        names, names_iata = _load_names()
        _apply(iata_map, names, names_iata)
    threading.Thread(target=_download, daemon=True, name="airlines").start()
    return _map


def iata_of(icao: str) -> str:
    code = (icao or "").strip().upper()
    if not code:
        return ""
    return _ensure().get(code, "")


def name_of(code: str) -> str:
    token = (code or "").strip().upper()
    if not token:
        return ""
    _ensure()
    if token in _names:
        return _names[token]
    if token in _names_iata:
        return _names_iata[token]
    return ""


def warmup() -> None:
    threading.Thread(target=_ensure, daemon=True, name="airlines").start()
