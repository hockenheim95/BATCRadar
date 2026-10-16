"""Session flight tracks: in memory, reset when the sim session ends."""

from __future__ import annotations

import math
import threading
import time

OWN_MAX_POINTS = 1600
OWN_KEEP_TAIL = 400
AI_MAX_POINTS = 600
AI_KEEP_TAIL = 120
OWN_MIN_DT = 0.8
OWN_MIN_MOVE = 0.00035
# AI live trail: one vertex roughly every AI_SAMPLE_SEC of ground track,
# clamped so approach stays denser and cruise stays sparse.
AI_MIN_KM = 0.08
AI_CRUISE_KM = 3.0
AI_SAMPLE_SEC = 10.0
# Taxi (on ground / ≤35 kt): denser than cruise so apron/taxi curves stay curved.
AI_TAXI_KT = 35.0
AI_TAXI_KM = 0.12
AI_TAXI_HDG_DEG = 5.0
AI_HDG_DEG = 4.0
AI_ALT_FT = 800.0
AI_MAX_DT = 8.0
JUMP_KM = 80.0
GLITCH_KM = 12.0
SESSION_GRACE = 25.0
TRACK_GRACE = 40.0
PARK_KM = 0.012


def _is_taxi(on_ground: bool, gs_kt: float) -> bool:
    return bool(on_ground) or float(gs_kt) <= AI_TAXI_KT


def _ai_need_km(gs_kt: float, on_ground: bool = False) -> float:
    """Min distance for a new AI track point (taxi uses turn gating instead)."""
    if _is_taxi(on_ground, gs_kt):
        return AI_TAXI_KM
    spaced = max(0.0, float(gs_kt)) * 1.852 * (AI_SAMPLE_SEC / 3600.0)
    return max(AI_MIN_KM, min(AI_CRUISE_KM, spaced))


def _path_turn_deg(buf: list[list[float]], lat: float, lon: float) -> float:
    """Turn at the last vertex toward (lat, lon); 0 if not enough points."""
    if len(buf) < 2:
        return 0.0
    return _turn_deg(buf[-2], buf[-1], [lat, lon])


def _airport_at(lat: float, lon: float) -> str:
    try:
        from airports import nearest

        hit = nearest(lat, lon, 5.0)
    except Exception:
        return ""
    return hit[0] if hit else ""


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    d1 = math.radians(lat2 - lat1)
    d2 = math.radians(lon2 - lon1)
    h = math.sin(d1 / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(d2 / 2) ** 2
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(h)))


def _hdg_delta(a: float | None, b: float | None) -> float:
    if a is None or b is None:
        return 0.0
    d = abs(a - b) % 360.0
    return d if d <= 180.0 else 360.0 - d


def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    y = math.sin(math.radians(lon2 - lon1)) * math.cos(math.radians(lat2))
    x = math.cos(math.radians(lat1)) * math.sin(math.radians(lat2)) - math.sin(
        math.radians(lat1)
    ) * math.cos(math.radians(lat2)) * math.cos(math.radians(lon2 - lon1))
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def _turn_deg(a: list[float], b: list[float], c: list[float]) -> float:
    d = abs(_bearing(a[0], a[1], b[0], b[1]) - _bearing(b[0], b[1], c[0], c[1])) % 360.0
    return d if d <= 180.0 else 360.0 - d


def _thin_even(buf: list[list[float]], max_pts: int, keep_tail: int) -> None:
    if len(buf) <= max_pts:
        return
    head = buf[0]
    tail = buf[-keep_tail:]
    mid = buf[1:-keep_tail]
    buf[:] = [head] + mid[::2] + tail
    if len(buf) > max_pts:
        del buf[1 : 1 + (len(buf) - max_pts)]


def _thin_curve(buf: list[list[float]], max_pts: int, keep_tail: int) -> None:
    if len(buf) <= max_pts:
        return
    head = buf[0]
    tail = buf[-keep_tail:] if keep_tail < len(buf) else buf[-1:]
    mid = buf[1 : len(buf) - len(tail)]
    kept: list[list[float]] = []
    for i, pt in enumerate(mid):
        if i == 0 or i + 1 >= len(mid):
            kept.append(pt)
            continue
        prev = kept[-1] if kept else head
        nxt = mid[i + 1]
        if _turn_deg(prev, pt, nxt) < 4.0 and _km(prev[0], prev[1], pt[0], pt[1]) < 8.0:
            continue
        kept.append(pt)
    buf[:] = [head] + kept + tail
    if len(buf) > max_pts:
        _thin_even(buf, max_pts, keep_tail)


class TrackStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pts: dict[str, list[list[float]]] = {}
        self._first: dict[str, dict] = {}
        self._stamp: dict[str, tuple[float, float, float, float | None, float]] = {}
        self._start_icao: dict[str, str] = {}
        self._field_alt: dict[str, float] = {}
        self._leg: dict[str, tuple[str, str]] = {}
        self._seen: dict[str, float] = {}
        self._alive_until = 0.0

    def touch_session(self) -> None:
        self._alive_until = time.monotonic() + SESSION_GRACE

    def maybe_reset(self) -> None:
        with self._lock:
            if self._pts and time.monotonic() > self._alive_until:
                self._wipe()

    def clear(self, keep_ids: set[str] | None = None) -> None:
        """Drop stored paths (BATC restart). Ownship from SimConnect may be kept."""
        keep = keep_ids or set()
        with self._lock:
            if not keep:
                self._wipe()
                return
            for key in list(self._pts):
                if key in keep:
                    continue
                self._pts.pop(key, None)
                self._first.pop(key, None)
                self._stamp.pop(key, None)
                self._start_icao.pop(key, None)
                self._field_alt.pop(key, None)
                self._leg.pop(key, None)
                self._seen.pop(key, None)

    def _wipe(self) -> None:
        self._pts.clear()
        self._first.clear()
        self._stamp.clear()
        self._start_icao.clear()
        self._field_alt.clear()
        self._leg.clear()
        self._seen.clear()

    def keep_only(self, ids: set[str]) -> None:
        now = time.monotonic()
        with self._lock:
            for key in ids:
                self._seen[key] = now
            drop = [
                key
                for key in list(self._pts)
                if key not in ids and now - self._seen.get(key, 0.0) > TRACK_GRACE
            ]
            for key in drop:
                self._pts.pop(key, None)
                self._first.pop(key, None)
                self._stamp.pop(key, None)
                self._start_icao.pop(key, None)
                self._field_alt.pop(key, None)
                self._leg.pop(key, None)
                self._seen.pop(key, None)

    def add(
        self,
        aid: str,
        lat: float,
        lon: float,
        on_ground: bool = False,
        alt: float | None = None,
        heading: float | None = None,
        gs: float | None = None,
        dense: bool = False,
    ) -> None:
        if not aid:
            return
        now = time.monotonic()
        alt_ft = float(alt) if alt is not None and alt == alt else 0.0
        hdg = None
        if heading is not None and heading == heading:
            hdg = float(heading) % 360.0
        gs_kt = float(gs) if gs is not None and gs == gs else 0.0
        with self._lock:
            self._seen[aid] = now
            prev = self._stamp.get(aid)
            if dense and prev:
                t0, la, lo, _ph, _pa = prev
                dlat = abs(la - lat)
                dlon = abs(lo - lon)
                if now - t0 < OWN_MIN_DT and dlat < OWN_MIN_MOVE and dlon < OWN_MIN_MOVE:
                    return
            buf = self._pts.setdefault(aid, [])
            if buf:
                last = buf[-1]
                jump = _km(last[0], last[1], lat, lon)
                if prev and not dense:
                    t0, _la, _lo, _ph, _pa = prev
                    dt = max(now - t0, 1e-3)
                    if jump > GLITCH_KM and jump < JUMP_KM:
                        implied_kt = (jump / 1.852) / (dt / 3600.0)
                        cap = max(gs_kt * 1.6, 120.0) + 80.0
                        if implied_kt > cap:
                            return
                if jump > JUMP_KM:
                    buf.clear()
                    self._first.pop(aid, None)
                    self._start_icao.pop(aid, None)
                    self._field_alt.pop(aid, None)
                elif on_ground and jump < PARK_KM:
                    self._stamp[aid] = (now, lat, lon, hdg, alt_ft)
                    return
                elif abs(last[0] - lat) < 3e-6 and abs(last[1] - lon) < 3e-6:
                    if len(last) < 3:
                        last.append(round(alt_ft))
                    else:
                        last[2] = round(alt_ft)
                    self._stamp[aid] = (now, lat, lon, hdg, alt_ft)
                    return
                elif not dense and buf:
                    prev_hdg = prev[3] if prev else None
                    d_alt = abs(alt_ft - float(last[2] if len(last) > 2 else alt_ft))
                    taxi = _is_taxi(on_ground, gs_kt)
                    need_km = _ai_need_km(gs_kt, on_ground)
                    if taxi:
                        # Geometry turn vs last edge — stamp heading slides each tick
                        # and would miss gradual taxiway bends.
                        if len(buf) >= 2:
                            hdg_lim = AI_TAXI_HDG_DEG
                            hdg_delta = _path_turn_deg(buf, lat, lon)
                        else:
                            hdg_lim = AI_TAXI_HDG_DEG
                            hdg_delta = _hdg_delta(prev_hdg, hdg)
                    else:
                        hdg_lim = AI_HDG_DEG
                        hdg_delta = _hdg_delta(prev_hdg, hdg)
                    dt = now - prev[0] if prev else 1e9
                    if (
                        jump < need_km
                        and hdg_delta < hdg_lim
                        and d_alt < AI_ALT_FT
                        and dt < AI_MAX_DT
                    ):
                        # Keep firstSeen frozen; slide the tail so /api/track
                        # always reaches "now" before anyone clicks the aircraft.
                        if len(buf) == 1:
                            if jump > 0.03:
                                buf.append([round(lat, 6), round(lon, 6), round(alt_ft)])
                        else:
                            prev_pt = buf[-2]
                            from_prev = _km(prev_pt[0], prev_pt[1], lat, lon)
                            if from_prev >= need_km:
                                buf.append([round(lat, 6), round(lon, 6), round(alt_ft)])
                            else:
                                last[0] = round(lat, 6)
                                last[1] = round(lon, 6)
                                if len(last) < 3:
                                    last.append(round(alt_ft))
                                else:
                                    last[2] = round(alt_ft)
                        self._stamp[aid] = (now, lat, lon, hdg, alt_ft)
                        return
            buf.append([round(lat, 6), round(lon, 6), round(alt_ft)])
            if len(buf) == 1:
                self._start_icao[aid] = _airport_at(lat, lon) if on_ground else ""
                self._first[aid] = {
                    "lat": round(lat, 6),
                    "lon": round(lon, 6),
                    "alt": round(alt_ft),
                    "hdg": round(hdg, 1) if hdg is not None else None,
                    "gs": round(gs_kt),
                    "onGround": bool(on_ground),
                }
            if on_ground and aid not in self._field_alt:
                self._field_alt[aid] = round(alt_ft)
            if dense:
                _thin_even(buf, OWN_MAX_POINTS, OWN_KEEP_TAIL)
            else:
                _thin_curve(buf, AI_MAX_POINTS, AI_KEEP_TAIL)
            self._stamp[aid] = (now, lat, lon, hdg, alt_ft)

    def reset(self, aid: str) -> None:
        """Drop the flown path so a new origin/dest leg starts clean."""
        if not aid:
            return
        with self._lock:
            self._pts.pop(aid, None)
            self._first.pop(aid, None)
            self._stamp.pop(aid, None)
            self._start_icao.pop(aid, None)
            self._field_alt.pop(aid, None)
            self._seen.pop(aid, None)

    def note_leg(self, aid: str, origin: str | None, dest: str | None) -> bool:
        """Reset the path when dest changes (turnaround: ATL–MIA becomes MIA–BWI)."""
        if not aid:
            return False
        o = str(origin or "").upper()
        d = str(dest or "").upper()
        with self._lock:
            prev = self._leg.get(aid)
            po, pd = prev if prev else ("", "")
            if o or d:
                self._leg[aid] = (o or po, d or pd)
            if prev and d and pd and d != pd:
                self._pts.pop(aid, None)
                self._first.pop(aid, None)
                self._stamp.pop(aid, None)
                self._start_icao.pop(aid, None)
                self._field_alt.pop(aid, None)
                return True
        return False

    def start_icao(self, aid: str) -> str:
        with self._lock:
            return self._start_icao.get(aid, "")

    def field_alt(self, aid: str) -> float | None:
        with self._lock:
            hit = self._field_alt.get(aid)
            return float(hit) if hit is not None else None

    def get(self, aid: str) -> list[list[float]]:
        with self._lock:
            return [pt[:] for pt in self._pts.get(aid, ())]

    def first_seen(self, aid: str) -> dict | None:
        """Where this flight leg was first observed; the live trail starts here."""
        with self._lock:
            hit = self._first.get(aid)
            return dict(hit) if hit else None

    def traffic_flags(self, aid: str) -> dict:
        with self._lock:
            buf = self._pts.get(aid, ())
            return {"trackReady": len(buf) > 0}
