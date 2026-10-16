"""Frozen recon prefix + live track (one polyline for /api/track).

Recon is built once when SID/STAR geometry is ready, then never mutated.
Live points are appended from firstSeen. BATC/server restart wipes RAM.
"""

from __future__ import annotations

import math

from navdata import _destination, _km, _km2

PHASE_NEAR_KM = 12.0
ON_LEG_KM = 0.8
RECON_MIN_SPAWN_KM = 8.0
RECON_MIN_SPAWN_SID_KM = 2.0
RUNWAY_LATERAL_KM = 0.4
RUNWAY_PAST_KM = 1.5
GC_POLAR_CAP_DEG = 70.0
NO_SID_CLIMB_NM = 5.0
AHEAD_CONE_DEG = 70.0
HAIRPIN_DEG = 135.0
LIVE_NEAR_KM = 2.0
LIVE_JOIN_KM = 8.0
LIVE_SLIDE_KM = 8.0
SID_TAIL_IGNORE_KM = 2.8
SID_TAIL_HOOK_KM = 8.0
SID_TAIL_HOOK_DEG = 50.0

_recon_prefix: dict[str, list[list[float]]] = {}
_recon_frozen: set[str] = set()


def invalidate(aid: str | None = None) -> None:
    if aid:
        _recon_prefix.pop(aid, None)
        _recon_frozen.discard(aid)
        return
    _recon_prefix.clear()
    _recon_frozen.clear()


def _heading_diff(a: float, b: float) -> float:
    d = abs(float(a) - float(b)) % 360.0
    return d if d <= 180.0 else 360.0 - d


def _bearing(a: list[float], b: list[float]) -> float:
    lat1, lon1 = a[0], a[1]
    lat2, lon2 = b[0], b[1]
    y = math.sin(math.radians(lon2 - lon1)) * math.cos(math.radians(lat2))
    x = math.cos(math.radians(lat1)) * math.sin(math.radians(lat2)) - math.sin(
        math.radians(lat1)
    ) * math.cos(math.radians(lat2)) * math.cos(math.radians(lon2 - lon1))
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def _project_on_seg(a: list[float], b: list[float], p: list[float]) -> tuple[float, list[float], float]:
    x1, y1 = a[1], a[0]
    x2, y2 = b[1], b[0]
    dx, dy = x2 - x1, y2 - y1
    den = dx * dx + dy * dy
    t = 0.0
    if den > 1e-12:
        t = ((p[1] - x1) * dx + (p[0] - y1) * dy) / den
    t = max(0.0, min(1.0, t))
    pt = [y1 + dy * t, x1 + dx * t]
    return t, pt, _km(pt, p[0], p[1])


def _nearest_on_path(path: list[list[float]], here: list[float]) -> tuple[float, int, float, list[float]]:
    best_d, best_i, best_t, best_pt = 1e9, 0, 0.0, here
    for i in range(len(path) - 1):
        for t_fix, p in ((0.0, path[i]), (1.0, path[i + 1])):
            d = _km2(p, here)
            if d < best_d:
                best_d, best_i, best_t, best_pt = d, i, t_fix, p
        t, pt, d = _project_on_seg(path[i], path[i + 1], here)
        if d < best_d:
            best_d, best_i, best_t, best_pt = d, i, t, pt
    return best_d, best_i, best_t, best_pt


def _spawn_ll(spawn: list[float] | dict) -> list[float]:
    if isinstance(spawn, dict):
        return [float(spawn["lat"]), float(spawn["lon"])]
    return [float(spawn[0]), float(spawn[1])]


def _spawn_pt(spawn: list[float] | dict) -> list[float]:
    if isinstance(spawn, dict):
        row = [float(spawn["lat"]), float(spawn["lon"])]
        alt = spawn.get("alt")
        if alt is not None:
            row.append(round(float(alt)))
        return row
    row = [float(spawn[0]), float(spawn[1])]
    if len(spawn) > 2 and spawn[2] is not None:
        row.append(spawn[2])
    return row


def _is_ahead(origin: list[float], pt: list[float], hdg: float | None) -> bool:
    if hdg is None:
        return False
    d = _km2(origin, pt)
    if d is None or d < 0.12:
        return False
    return _heading_diff(_bearing(origin, pt), float(hdg)) < AHEAD_CONE_DEG


def _clip_path_behind_spawn(path: list[list[float]], spawn: list[float], hdg: float | None) -> list[list[float]]:
    """Keep procedure vertices at/behind spawn. Does not append spawn."""
    if len(path) < 2:
        return [p[:] for p in path]
    along = 0.0
    best = None
    nearest = None
    for i in range(len(path) - 1):
        seg = _km2(path[i], path[i + 1]) or 0.0
        t, pt, d = _project_on_seg(path[i], path[i + 1], spawn)
        s = along + seg * t
        if nearest is None or d < nearest[0]:
            nearest = (d, s, i, t, pt)
        # t≈1 belongs to the next leg; a 12 km blob also hits every earlier end.
        if t < 0.995 and d < ON_LEG_KM and (best is None or s < best[0]):
            best = (s, i, t, d, pt)
        along += seg
    if best is None:
        if nearest is None or nearest[0] >= PHASE_NEAR_KM:
            return [p[:] for p in path]
        _, _, bi, bt, bpt = nearest
        best = (0.0, bi, bt, nearest[0], bpt)
    _, bi, bt, bd, bpt = best
    out = [p[:] for p in path[: bi + 1]]
    nxt = path[bi + 1]
    leg_brg = _bearing(path[bi], nxt)
    on_line = (
        bt > 0.02
        and bd < 0.6
        and (hdg is None or _heading_diff(leg_brg, float(hdg)) < 45.0)
    )
    near_term = (_km2(nxt, spawn) or 0.0) < SID_TAIL_IGNORE_KM
    if on_line and not near_term:
        out.append([bpt[0], bpt[1]])
    elif bt > 0.98 and not near_term:
        _append_pt(out, nxt[:])
    return out if out else [p[:] for p in path[:1]]


def _drop_ahead_of_spawn(path: list[list[float]], spawn: list[float], hdg: float | None) -> list[list[float]]:
    """Pop trailing vertices that sit in front of the aircraft at spawn."""
    out = [p[:] for p in path]
    while len(out) >= 2 and _is_ahead(spawn, out[-1], hdg):
        out.pop()
    return out


def _drop_sid_tail_near_spawn(path: list[list[float]], spawn: list[float]) -> list[list[float]]:
    """Drop SID terminator(s) sitting beside spawn so the join is previous fix → spawn."""
    out = [p[:] for p in path]
    while len(out) >= 2:
        last, prev = out[-1], out[-2]
        d_last = _km2(last, spawn) or 0.0
        d_prev = _km2(prev, spawn) or 0.0
        turn = _heading_diff(_bearing(prev, last), _bearing(last, spawn))
        near_term = d_last < SID_TAIL_IGNORE_KM
        hook = d_last < SID_TAIL_HOOK_KM and turn > SID_TAIL_HOOK_DEG
        overshoot = d_prev + 0.4 < d_last and d_last < PHASE_NEAR_KM
        if near_term or hook or overshoot:
            out.pop()
            continue
        break
    return out


def _drop_hairpins(pts: list[list[float]]) -> list[list[float]]:
    """Remove a last-leg hook that doubles back toward spawn (zigzag)."""
    while len(pts) >= 3:
        turn = _heading_diff(_bearing(pts[-3], pts[-2]), _bearing(pts[-2], pts[-1]))
        last_leg = _km2(pts[-2], pts[-1]) or 0.0
        prev_leg = _km2(pts[-3], pts[-2]) or 0.0
        if turn > HAIRPIN_DEG and last_leg < 20.0 and prev_leg < 25.0:
            pts.pop(-2)
            continue
        break
    return pts


def _turn_radius_km(kt: float, bank_deg: float) -> float:
    v = max(80.0, kt) * 0.514444
    tan_b = math.tan(math.radians(bank_deg))
    return (v * v) / (9.80665 * tan_b) / 1000.0


def _speed_for_turn(along_km: float) -> float:
    if along_km < 12.0:
        return 185.0
    if along_km < 35.0:
        return 220.0
    if along_km < 90.0:
        return 250.0
    if along_km < 250.0:
        return 340.0
    return 420.0


def _to_xy(p: list[float], o: list[float]) -> tuple[float, float]:
    k = 111.32
    return (
        (p[1] - o[1]) * k * math.cos(math.radians(o[0])),
        (p[0] - o[0]) * k,
    )


def _from_xy(xy: tuple[float, float], o: list[float]) -> list[float]:
    k = 111.32
    return [
        o[0] + xy[1] / k,
        o[1] + xy[0] / (k * math.cos(math.radians(o[0]))),
    ]


def _unit(vx: float, vy: float) -> tuple[float, float]:
    n = math.hypot(vx, vy) or 1.0
    return vx / n, vy / n


def _course_delta(u: tuple[float, float], v: tuple[float, float]) -> tuple[float, bool]:
    cr = u[0] * v[1] - u[1] * v[0]
    dt = max(-1.0, min(1.0, u[0] * v[0] + u[1] * v[1]))
    return math.acos(dt), cr > 0


def _ease_sharp_corners(pts: list[list[float]]) -> list[list[float]]:
    if len(pts) < 3:
        return [p[:] for p in pts]
    out: list[list[float]] = [pts[0][:]]
    for i in range(1, len(pts) - 1):
        prev, cur, nxt = pts[i - 1], pts[i], pts[i + 1]
        d1 = _km2(prev, cur) or 0.0
        d2 = _km2(cur, nxt) or 0.0
        out.append(cur[:])
        if d1 < 1.0 or d2 < 1.0 or abs(cur[0]) > 72.0:
            continue
        a = _to_xy(prev, cur)
        b = _to_xy(nxt, cur)
        u = _unit(-a[0], -a[1])
        v = _unit(b[0], b[1])
        ang, left = _course_delta(u, v)
        if math.degrees(ang) < 110.0:
            continue
        L = max(3.0, min(22.0, min(d1, d2) * 0.4))
        n = (-u[1], u[0]) if left else (u[1], -u[0])
        w1 = (u[0] * 0.5 * L + n[0] * 0.6 * L, u[1] * 0.5 * L + n[1] * 0.6 * L)
        w2 = (-u[0] * 0.3 * L + n[0] * 1.6 * L, -u[1] * 0.3 * L + n[1] * 1.6 * L)
        out.append(_from_xy(w1, cur))
        out.append(_from_xy(w2, cur))
    out.append(pts[-1][:])
    return out


def _smooth_recon_path(pts: list[list[float]]) -> list[list[float]]:
    """Fillet procedure corners with bank-limited turn radius (~25° bank)."""
    pts = _ease_sharp_corners(pts)
    if len(pts) < 3:
        return pts
    out: list[list[float]] = [pts[0][:]]
    along = 0.0
    for i in range(1, len(pts) - 1):
        prev, cur, nxt = pts[i - 1], pts[i], pts[i + 1]
        d1 = _km2(prev, cur) or 0.0
        d2 = _km2(cur, nxt) or 0.0
        along += d1
        if d1 < 0.35 or d2 < 0.35 or abs(cur[0]) > 72.0:
            out.append(cur[:])
            continue
        o = cur
        a = _to_xy(prev, o)
        b = _to_xy(nxt, o)
        u = _unit(-a[0], -a[1])
        v = _unit(b[0], b[1])
        ang, left = _course_delta(u, v)
        deg = math.degrees(ang)
        if deg < 7.0 or deg > 178.0:
            out.append(cur[:])
            continue
        r = _turn_radius_km(_speed_for_turn(along), 25.0)
        if deg > 40.0:
            r = max(r, 5.0)
        half = math.tan(ang / 2.0)
        if half < 1e-3:
            out.append(cur[:])
            continue
        lead = r * half
        cap = min(d1, d2) * 0.48
        if lead > cap:
            lead = cap
            r = lead / half
        if r < 0.35 or lead < 0.2:
            out.append(cur[:])
            continue
        a_xy = (-u[0] * lead, -u[1] * lead)
        b_xy = (v[0] * lead, v[1] * lead)
        n = (-u[1], u[0]) if left else (u[1], -u[0])
        ctr = (a_xy[0] + n[0] * r, a_xy[1] + n[1] * r)
        s_ang = math.atan2(a_xy[1] - ctr[1], a_xy[0] - ctr[0])
        e_ang = math.atan2(b_xy[1] - ctr[1], b_xy[0] - ctr[0])
        sweep = e_ang - s_ang
        if left and sweep < 0:
            sweep += 2.0 * math.pi
        if not left and sweep > 0:
            sweep -= 2.0 * math.pi
        if abs(sweep) < 0.05:
            out.append(cur[:])
            continue
        steps = max(4, min(18, int(math.ceil((r * abs(sweep)) / 0.7))))
        out.append(_from_xy(a_xy, o))
        for s in range(1, steps):
            t = s / steps
            an = s_ang + sweep * t
            out.append(_from_xy((ctr[0] + math.cos(an) * r, ctr[1] + math.sin(an) * r), o))
        out.append(_from_xy(b_xy, o))
    out.append(pts[-1][:])
    return out


def _close_recon_at_spawn(path: list[list[float]], spawn: list[float] | dict, hdg: float | None) -> list[list[float]]:
    """Clip procedure behind spawn, skip a nearby last fix, end exactly at spawn."""
    dest = _spawn_pt(spawn)
    if not path:
        return [dest]
    out = _clip_path_behind_spawn(path, dest, hdg)
    out = _drop_ahead_of_spawn(out, dest, hdg)
    out = _drop_sid_tail_near_spawn(out, dest)
    if not out:
        return [dest]
    gap = _km2(out[-1], dest) or 0.0
    if gap < 0.08:
        out[-1] = dest
    elif gap < PHASE_NEAR_KM:
        _append_pt(out, dest)
    else:
        _append_gc_to(out, dest)
        if _km2(out[-1], dest) >= 0.08:
            out.append(dest)
        else:
            out[-1] = dest
    _drop_hairpins(out)
    if len(out) >= 4:
        out = _smooth_recon_path(out)
    out[-1] = dest
    return out


def _spawn_along_runway_km(plane: dict, spawn: list[float]) -> float:
    lat, lon = plane.get("depRwyLat"), plane.get("depRwyLon")
    hdg = plane.get("depRwyHdg")
    if lat is None or lon is None or hdg is None:
        return _km([lat or spawn[0], lon or spawn[1]], spawn[0], spawn[1])
    start = [float(lat), float(lon)]
    far = _destination(start[0], start[1], float(hdg), 120.0)
    t, _, _ = _project_on_seg(start, list(far), spawn)
    span = _km2(start, list(far)) or 1.0
    return max(0.0, t * span)


def _append_pt(out: list[list[float]], pt: list[float]) -> None:
    if not out:
        out.append(pt)
        return
    if _km2(out[-1], pt) >= 0.04:
        out.append(pt)


def _slerp_gc(a: list[float], b: list[float]) -> list[list[float]]:
    d = _km2(a, b)
    if d is None:
        return []
    if d < 3.0:
        return [a[:2], b[:2]]
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    x1 = math.cos(lat1) * math.cos(lon1)
    y1 = math.cos(lat1) * math.sin(lon1)
    z1 = math.sin(lat1)
    x2 = math.cos(lat2) * math.cos(lon2)
    y2 = math.cos(lat2) * math.sin(lon2)
    z2 = math.sin(lat2)
    dot = max(-1.0, min(1.0, x1 * x2 + y1 * y2 + z1 * z2))
    om = math.acos(dot)
    if om < 1e-6:
        return [a[:2], b[:2]]
    step = 90.0 if d > 4000.0 else 55.0 if d > 1500.0 else 28.0
    n = max(2, min(160, int(math.ceil(d / step))))
    out: list[list[float]] = []
    sin_om = math.sin(om)
    for i in range(n + 1):
        t = i / n
        s1 = math.sin((1.0 - t) * om) / sin_om
        s2 = math.sin(t * om) / sin_om
        x = s1 * x1 + s2 * x2
        y = s1 * y1 + s2 * y2
        z = s1 * z1 + s2 * z2
        lat = math.degrees(math.atan2(z, math.hypot(x, y)))
        lon = math.degrees(math.atan2(y, x))
        if out:
            prev_lon = out[-1][1]
            while lon - prev_lon > 180.0:
                lon -= 360.0
            while lon - prev_lon < -180.0:
                lon += 360.0
        out.append([lat, lon])
    return out


def _gc_peak(pts: list[list[float]]) -> list[float]:
    peak = pts[0]
    for p in pts:
        if abs(p[0]) > abs(peak[0]):
            peak = p
    return peak


def _gc_points(a: list[float], b: list[float], depth: int = 0) -> list[list[float]]:
    pts = _slerp_gc(a, b)
    if len(pts) < 2:
        return pts
    peak = _gc_peak(pts)
    if abs(peak[0]) <= GC_POLAR_CAP_DEG or depth > 3:
        return pts
    way = [-GC_POLAR_CAP_DEG if peak[0] < 0 else GC_POLAR_CAP_DEG, peak[1]]
    if _km2(a, way) < 40.0 or _km2(way, b) < 40.0:
        return pts
    left = _gc_points(a, way, depth + 1)
    right = _gc_points(way, b, depth + 1)
    return left + right[1:]


def _append_gc_to(out: list[list[float]], dest: list[float]) -> None:
    if not out:
        out.append([dest[0], dest[1]])
        return
    last = out[-1]
    dest_pt = [dest[0], dest[1]]
    gap = _km2(last, dest_pt) or 0.0
    if gap < 0.08:
        out[-1] = dest_pt
        return
    if gap < PHASE_NEAR_KM:
        _append_pt(out, dest_pt)
        return
    for p in _gc_points(last, dest_pt)[1:]:
        _append_pt(out, [p[0], p[1]])


def _runway_start(plane: dict) -> list[float] | None:
    lat, lon = plane.get("depRwyLat"), plane.get("depRwyLon")
    if lat is None or lon is None:
        return None
    return [float(lat), float(lon)]


def _runway_end(plane: dict, start: list[float], rwy_hdg: float) -> list[float]:
    if plane.get("depRwyEndLat") is not None:
        return [float(plane["depRwyEndLat"]), float(plane["depRwyEndLon"])]
    return list(_destination(start[0], start[1], rwy_hdg, 2.0))


def _on_runway(plane: dict, spawn: list[float]) -> bool:
    """True if the airborne spawn is still over the departure runway."""
    start = _runway_start(plane)
    hdg = plane.get("depRwyHdg")
    if start is None or hdg is None:
        return False
    rwy_hdg = float(hdg)
    end = _runway_end(plane, start, rwy_hdg)
    far = _destination(start[0], start[1], rwy_hdg, 120.0)
    _t, _pt, lateral = _project_on_seg(start, list(far), spawn)
    if lateral > RUNWAY_LATERAL_KM:
        return False
    along = _spawn_along_runway_km(plane, spawn)
    runway_km = _km2(start, end) or 3.0
    return -0.2 <= along <= runway_km + RUNWAY_PAST_KM


def _build_no_sid_geometry(plane: dict, spawn: list[float]) -> list[list[float]]:
    """Threshold → DER → straight min(5 NM, spawn on heading). Caller closes at spawn."""
    start = _runway_start(plane)
    if start is None:
        return []
    rwy_hdg = plane.get("depRwyHdg")
    if rwy_hdg is None:
        return [start]
    rwy_hdg = float(rwy_hdg)
    end = _runway_end(plane, start, rwy_hdg)
    out: list[list[float]] = [start]
    if (_km2(start, end) or 0.0) > 0.02:
        out.append(end)
    climb_nm = NO_SID_CLIMB_NM
    on_hdg = _heading_diff(rwy_hdg, _bearing(start, spawn)) < 45.0
    if on_hdg:
        past_der_km = max(0.0, _spawn_along_runway_km(plane, spawn) - (_km2(start, end) or 0.0))
        climb_nm = min(NO_SID_CLIMB_NM, past_der_km / 1.852)
    if climb_nm > 0.05:
        climb = _destination(end[0], end[1], rwy_hdg, climb_nm)
        _append_pt(out, [climb[0], climb[1]])
    return out


def _past_procedure(path: list[list[float]], origin: list[float], spawn: list[float]) -> bool:
    if len(path) < 2:
        return False
    return (_km2(origin, spawn) or 0.0) > (_km2(origin, path[-1]) or 0.0) - 4.0


def _departure_part(
    plane: dict, spawn: list[float], sid: list, hdg: float | None
) -> list[list[float]]:
    start = _runway_start(plane)
    origin = start
    if origin is None and plane.get("originLat") is not None:
        origin = [float(plane["originLat"]), float(plane["originLon"])]
    if len(sid) > 1:
        d_sid = _nearest_on_path(sid, spawn)[0]
        if d_sid < PHASE_NEAR_KM:
            return _clip_path_behind_spawn(sid, spawn, hdg)
        if origin and _past_procedure(sid, origin, spawn):
            return [p[:] for p in sid]
    return _build_no_sid_geometry(plane, spawn)


def _min_spawn_km(plane: dict, spawn: list[float], sid: list[list[float]] | None) -> float:
    min_km = RECON_MIN_SPAWN_KM
    sid_pts = [p[:] for p in (sid or []) if p]
    if len(sid_pts) > 1:
        d_sid, _, _, _ = _nearest_on_path(sid_pts, spawn)
        if d_sid < PHASE_NEAR_KM:
            min_km = RECON_MIN_SPAWN_SID_KM
    return min_km


def recon_anchor(plane: dict | None, first_seen: dict | None, paths: dict) -> dict | None:
    """Recon joins at first_seen when that observation is airborne departure, not takeoff roll."""
    if not plane or not first_seen:
        return None
    sid = paths.get("sidPath") or []
    spawn = _spawn_ll(first_seen)
    if not _needs_recon(plane, first_seen, spawn, sid):
        return None
    return first_seen


def _needs_recon(
    plane: dict, first_seen: dict, spawn: list[float], sid: list[list[float]] | None = None
) -> bool:
    """Recon only when BATC's first observation is already airborne and past takeoff."""
    if first_seen.get("onGround"):
        return False
    start = _runway_start(plane)
    if start is None and plane.get("originLat") is not None:
        start = [float(plane["originLat"]), float(plane["originLon"])]
    if start is None:
        return False
    if (_km2(start, spawn) or 0.0) < _min_spawn_km(plane, spawn, sid):
        return False
    if _on_runway(plane, spawn):
        return False
    return True


def _build_recon_prefix(plane: dict, sid: list, star: list, recon_from: dict) -> list[list[float]]:
    spawn = _spawn_ll(recon_from)
    hdg = recon_from.get("hdg")
    hdg_f = float(hdg) if hdg is not None else None
    sid = [p[:] for p in (sid or []) if p]
    star = [p[:] for p in (star or []) if p]
    if not _needs_recon(plane, recon_from, spawn, sid):
        return []

    pts = _departure_part(plane, spawn, sid, hdg_f)
    if len(star) > 1 and _nearest_on_path(star, spawn)[0] < PHASE_NEAR_KM:
        star_seg = _clip_path_behind_spawn(star, spawn, hdg_f)
        if star_seg:
            if pts:
                _append_gc_to(pts, star_seg[0])
                for p in star_seg[1:]:
                    _append_pt(pts, p)
            else:
                pts = star_seg
    return _close_recon_at_spawn(pts, recon_from, hdg_f)


def _paths_ready(plane: dict, paths: dict, recon_from: dict) -> bool:
    """Freeze after first SID/STAR geometry; SimConnect may still be empty on first ticks."""
    if str(plane.get("sid") or "").strip() and len(paths.get("sidPath") or []) < 2:
        return False
    if str(plane.get("star") or "").strip() and len(paths.get("starPath") or []) < 2:
        spawn = _spawn_ll(recon_from)
        dlat, dlon = plane.get("destLat"), plane.get("destLon")
        if dlat is not None and dlon is not None:
            if (_km2(spawn, [float(dlat), float(dlon)]) or 0.0) < 280.0:
                return False
    return True


def recon_prefix_for(aid: str, plane: dict | None, paths: dict, recon_from: dict | None) -> list[list[float]]:
    if not aid or not plane or not recon_from:
        return []
    if aid in _recon_frozen and aid in _recon_prefix:
        return [p[:] for p in _recon_prefix[aid]]
    pts = _build_recon_prefix(plane, paths.get("sidPath") or [], paths.get("starPath") or [], recon_from)
    _recon_prefix[aid] = [p[:] for p in pts]
    if pts and _paths_ready(plane, paths, recon_from):
        _recon_frozen.add(aid)
    return [p[:] for p in pts]


def _sanitize_live(pts: list[list[float]]) -> list[list[float]]:
    """Drop BATC tail-slide near spawn and tiny GPS hairpins — keep the rest intact."""
    if len(pts) < 2:
        return pts
    spawn = pts[0]
    out: list[list[float]] = [pts[0][:]]
    max_d = 0.0
    for p in pts[1:]:
        d = _km2(spawn, p) or 0.0
        if d < LIVE_SLIDE_KM and d + 0.12 < max_d:
            continue
        if d < LIVE_SLIDE_KM:
            max_d = max(max_d, d)
        if len(out) >= 2:
            turn = _heading_diff(_bearing(out[-2], out[-1]), _bearing(out[-1], p))
            short = (_km2(out[-1], p) or 0.0) < 1.5 and (_km2(out[-2], out[-1]) or 0.0) < 1.5
            if turn > 150.0 and short:
                out.pop()
        if out and (_km2(out[-1], p) or 0.0) < 0.04:
            out[-1] = p[:]
            continue
        out.append(p[:])
    return out if out else pts


def _live_from_spawn(live: list[list[float]], spawn: dict) -> list[list[float]]:
    """Measured path starts at airborne spawn; never include taxi/gate before it."""
    here = _spawn_pt(spawn)
    hdg = spawn.get("hdg")
    hdg_f = float(hdg) if hdg is not None else None
    if not live:
        return [here]
    i0: int | None = None
    best_i = 0
    best_d = 1e9
    for i, p in enumerate(live):
        d = _km2(p, here) or 1e9
        if d < best_d:
            best_d, best_i = d, i
        if i0 is None and d < LIVE_NEAR_KM:
            i0 = i
            break
    if i0 is None and best_d < LIVE_JOIN_KM:
        i0 = best_i
    if i0 is None:
        out = [here]
        for p in live:
            d = _km2(here, p) or 0.0
            if d < 0.15:
                continue
            if hdg_f is not None and _heading_diff(_bearing(here, p), hdg_f) > 100.0:
                continue
            out.append(p[:])
        return _sanitize_live(out)
    out = [here]
    for p in live[i0 + 1 :]:
        out.append(p[:])
    return _sanitize_live(out)


def combined_trail(
    aid: str,
    plane: dict | None,
    paths: dict,
    live_pts: list[list[float]],
    recon_from: dict | None,
) -> tuple[list[list[float]], int]:
    if not live_pts:
        return [], 0
    live = [p[:] for p in live_pts]
    if not recon_from:
        return live, 0

    tail = _live_from_spawn(live, recon_from)
    prefix = recon_prefix_for(aid, plane, paths, recon_from) if plane else []
    dest = _spawn_pt(recon_from)
    if not prefix:
        if tail:
            tail[0] = dest
        return tail, 0

    prefix[-1] = dest
    if tail:
        tail[0] = dest
        combined = prefix + tail[1:]
    else:
        combined = prefix[:]
    return combined, len(prefix)
