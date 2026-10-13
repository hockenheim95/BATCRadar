"""PC-side OSM tile + airline-logo cache so the kiosk only talks to the LAN."""

from __future__ import annotations

import hashlib
import queue
import threading
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
TILE_DIR = ROOT / "data" / "tilecache"
LOGO_DIR = ROOT / "data" / "logocache"
LOGO_OVERRIDE = ROOT / "data" / "logos"
OSM_URLS = (
    "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    "https://tile.openstreetmap.de/{z}/{x}/{y}.png",
)
# Priority order (first usable wins). Google = symbol-only tailfin logos (preferred);
# the others are wordmark fallbacks for airlines Google only has a placeholder for.
LOGO_URLS = (
    "https://www.gstatic.com/flights/airline_logos/70px/{iata}.png",
    "https://images.kiwi.com/airlines/128/{iata}.png",
    "https://images.kiwi.com/airlines/64/{iata}.png",
    "https://pics.avs.io/200/200/{iata}.png",
)
# Generic "missing airline" artwork served as a real PNG.
LOGO_PLACEHOLDERS = {
    "b9ffde2bf8c2a7cb9eb6ee601ceece2a477b76157b8256290819c508832952fa",  # Google tailfin
    "b34521e5d601195d58a33224df666c96ba9e43491209142133aefbc5c594b3c7",  # Kiwi airplane
}
MAX_ZOOM = 18
TILE_BUDGET = 400 * 1024 * 1024
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_TILE_WORKERS = 4
_osm_pause_until = 0.0
_inflight: dict[tuple[int, int, int], threading.Event] = {}
_inflight_lock = threading.Lock()
_tile_jobs: queue.Queue[tuple[int, int, int, str]] = queue.Queue(maxsize=96)
_tile_workers_started = False

_lock = threading.Lock()
_last_prune = 0.0


def _ua(version: str) -> str:
    return f"EFB-Moving-Map/{version} (local kiosk tile proxy)"


def _fetch(url: str, version: str, timeout: float = 10.0) -> bytes | None:
    global _osm_pause_until
    req = Request(url, headers={"User-Agent": _ua(version), "Accept": "image/png,*/*"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            data = resp.read()
    except HTTPError as exc:
        if exc.code in (429, 503):
            _osm_pause_until = time.time() + 2.0
        return None
    except (URLError, TimeoutError, OSError):
        return None
    if not data or not data.startswith(PNG_MAGIC):
        return None
    return data


def _fetch_osm_tile(z: int, x: int, y: int, version: str) -> bytes | None:
    if time.time() < _osm_pause_until:
        return None
    delay = 0.15
    for url_tmpl in OSM_URLS:
        url = url_tmpl.format(z=z, x=x, y=y)
        data = _fetch(url, version, timeout=4.0)
        if data:
            return data
        if time.time() < _osm_pause_until:
            return None
        time.sleep(delay)
        delay = min(delay * 2, 0.8)
    return None


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _prune_tiles() -> None:
    global _last_prune
    now = time.time()
    if now - _last_prune < 120:
        return
    _last_prune = now
    if not TILE_DIR.is_dir():
        return
    files = [p for p in TILE_DIR.rglob("*.png") if p.is_file()]
    total = sum(p.stat().st_size for p in files)
    if total <= TILE_BUDGET:
        return
    files.sort(key=lambda p: p.stat().st_mtime)
    for path in files:
        try:
            total -= path.stat().st_size
            path.unlink()
        except OSError:
            continue
        if total <= TILE_BUDGET * 0.8:
            break


def _valid_tile(z: int, x: int, y: int) -> bool:
    if z < 0 or z > MAX_ZOOM:
        return False
    n = 1 << z
    return 0 <= x < n and 0 <= y < n


def _tile_path(z: int, x: int, y: int) -> Path:
    return TILE_DIR / str(z) / str(x) / f"{y}.png"


def _read_cache(z: int, x: int, y: int) -> bytes | None:
    path = _tile_path(z, x, y)
    if not path.is_file() or path.stat().st_size <= 32:
        return None
    try:
        blob = path.read_bytes()
    except OSError:
        return None
    if blob.startswith(PNG_MAGIC) and len(blob) > 32:
        return blob
    try:
        path.unlink()
    except OSError:
        pass
    return None


def _store_tile(z: int, x: int, y: int, data: bytes) -> None:
    with _lock:
        try:
            _write(_tile_path(z, x, y), data)
            _prune_tiles()
        except OSError:
            pass


def _tile_worker() -> None:
    while True:
        z, x, y, version = _tile_jobs.get()
        key = (z, x, y)
        try:
            if _read_cache(z, x, y):
                continue
            data = _fetch_osm_tile(z, x, y, version)
            if data:
                _store_tile(z, x, y, data)
        except Exception:
            pass
        finally:
            with _inflight_lock:
                ev = _inflight.pop(key, None)
            if ev is not None:
                ev.set()


def start_tile_workers() -> None:
    global _tile_workers_started
    if _tile_workers_started:
        return
    _tile_workers_started = True
    for i in range(_TILE_WORKERS):
        threading.Thread(target=_tile_worker, name=f"osm-tile-{i}", daemon=True).start()


def _enqueue_tile(z: int, x: int, y: int, version: str) -> threading.Event:
    key = (z, x, y)
    with _inflight_lock:
        wait = _inflight.get(key)
        if wait is not None:
            return wait
        wait = threading.Event()
        _inflight[key] = wait
    try:
        _tile_jobs.put_nowait((z, x, y, version))
    except queue.Full:
        with _inflight_lock:
            _inflight.pop(key, None)
        wait.set()
    return wait


def tile_png(z: int, x: int, y: int, version: str) -> bytes | None:
    """Return a cached OSM PNG quickly; miss fetches run on worker threads."""
    if not _valid_tile(z, x, y):
        return None
    cached = _read_cache(z, x, y)
    if cached:
        return cached
    start_tile_workers()
    wait = _enqueue_tile(z, x, y, version)
    wait.wait(timeout=0.4)
    return _read_cache(z, x, y)


def _usable_logo(data: bytes | None) -> bool:
    if not data or not data.startswith(PNG_MAGIC) or len(data) < 900:
        return False
    digest = hashlib.sha256(data).hexdigest()
    return digest not in LOGO_PLACEHOLDERS


def _fetch_best_logo(code: str, version: str) -> bytes | None:
    for tmpl in LOGO_URLS:
        cand = _fetch(tmpl.format(iata=code), version, timeout=6.0)
        if _usable_logo(cand):
            return cand
    return None


def logo_png(iata: str, version: str) -> bytes | None:
    code = "".join(ch for ch in (iata or "").upper() if ch.isalnum())[:3]
    if len(code) < 2:
        return None
    override = LOGO_OVERRIDE / f"{code}.png"
    if override.is_file():
        blob = override.read_bytes()
        if _usable_logo(blob):
            return blob
    path = LOGO_DIR / f"{code}.png"
    if path.is_file():
        cached = path.read_bytes()
        if _usable_logo(cached):
            return cached
        try:
            path.unlink()
        except OSError:
            pass
    data = _fetch_best_logo(code, version)
    if not data:
        return None
    try:
        _write(path, data)
    except OSError:
        pass
    return data
