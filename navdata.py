"""SID/STAR, airport and runway geometry, all from the running sim (SimConnect Facility Data).

There is no external navdata database: whatever AIRAC cycle MSFS has loaded is what we use.
See msfs_procs.py. While an airport is still being fetched, lookups return nothing (and are not
cached), so paths simply appear a moment later.
"""

from __future__ import annotations

import math
import random
import re
import threading
from collections import OrderedDict

import msfs_procs

_lock = threading.Lock()
_rwy_cache: dict[tuple, tuple[float, float, float] | None] = {}
_rwy_list: dict[tuple, list[tuple[str, float, float, float]]] = {}
_proc_cache: dict[tuple, dict[tuple[str, str], list] | None] = {}
_name_cache: dict[tuple, list[tuple[str, str]]] = {}
_sid_geom: dict[tuple, list[list[float]]] = {}


def _stamp(icao: str) -> int | None:
    """Fetch stamp of the cached sim data for this airport, or None while not available."""
    data = msfs_procs.airport(icao)
    if not data:
        return None
    return int(data.get("fetched") or 0)


def _destination(lat: float, lon: float, heading: float, nm: float) -> tuple[float, float]:
    r = 3440.065
    brng = math.radians(heading)
    lat1 = math.radians(lat)
    lon1 = math.radians(lon)
    lat2 = math.asin(
        math.sin(lat1) * math.cos(nm / r)
        + math.cos(lat1) * math.sin(nm / r) * math.cos(brng)
    )
    lon2 = lon1 + math.atan2(
        math.sin(brng) * math.sin(nm / r) * math.cos(lat1),
        math.cos(nm / r) - math.sin(lat1) * math.sin(lat2),
    )
    return math.degrees(lat2), math.degrees(lon2)


def _km(a: list[float], lat: float, lon: float) -> float:
    r = 6371.0
    p1 = math.radians(a[0])
    p2 = math.radians(lat)
    d1 = math.radians(lat - a[0])
    d2 = math.radians(lon - a[1])
    h = math.sin(d1 / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(d2 / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


def _path_km(pts: list[list[float]], lat: float, lon: float) -> float:
    if not pts:
        return 1e9
    return min(_km(pt, lat, lon) for pt in pts)


def _norm_rwy(text: str) -> str:
    raw = (text or "").strip().upper().replace("RWY", "").replace("RUNWAY", "").replace("RW", "")
    if raw.startswith("0") and len(raw) >= 2 and raw[1].isdigit():
        raw = raw.lstrip("0") or "0"
    return raw


def _rwy_parts(ident: str) -> tuple[str, str]:
    n = _norm_rwy(ident)
    if not n:
        return "", ""
    if n[-1:].isalpha() and (n[:-1].isdigit() or not n[:-1]):
        body = (n[:-1].lstrip("0") or "0") if n[:-1] else "0"
        return body, n[-1]
    if n.isdigit():
        return n.lstrip("0") or "0", ""
    return n, ""


def _rwy_tokens(ident: str) -> set[str]:
    n = _norm_rwy(ident)
    if not n:
        return set()
    body, suf = _rwy_parts(n)
    padded = body.zfill(2) + suf
    raw = body + suf
    return {n, padded, raw, "RW" + n, "RW" + padded, "RW" + raw}


def _same_rwy(a: str, b: str) -> bool:
    na, sa = _rwy_parts(a)
    nb, sb = _rwy_parts(b)
    if not na or not nb:
        return False
    return na == nb and sa == sb


def _trans_match(trans: str, ident: str) -> bool:
    t = (trans or "").upper()
    if not t or t == "ALL":
        return False
    return _same_rwy(t, ident)


def airport_info(icao: str) -> tuple[str, str, float, float] | None:
    code = (icao or "").strip().upper()
    if len(code) != 4:
        return None
    data = msfs_procs.airport(code)
    apt = (data or {}).get("airport")
    if not apt:
        return None
    return (code, apt.get("name") or code, float(apt["lat"]), float(apt["lon"]))


def list_runways(icao: str) -> list[tuple[str, float, float, float]]:
    """(ident, lat, lon, true heading) for every runway end, threshold position."""
    code = (icao or "").strip().upper()
    if len(code) != 4:
        return []
    data = msfs_procs.airport(code)
    if not data:
        return []
    key = (code, int(data.get("fetched") or 0))
    with _lock:
        if key in _rwy_list:
            return _rwy_list[key]
    out: list[tuple[str, float, float, float]] = []
    for rw in data.get("runways") or []:
        hdg = float(rw.get("hdg") or 0.0) % 360.0
        half_nm = float(rw.get("len") or 0.0) / 2.0 / 1852.0
        lat, lon = float(rw["lat"]), float(rw["lon"])
        back = _destination(lat, lon, (hdg + 180.0) % 360.0, half_nm)
        fwd = _destination(lat, lon, hdg, half_nm)
        pri = (rw.get("pri") or "").upper()
        sec = (rw.get("sec") or "").upper()
        if pri and not pri.startswith("00"):
            out.append((pri, back[0], back[1], hdg))
        if sec and not sec.startswith("00"):
            out.append((sec, fwd[0], fwd[1], (hdg + 180.0) % 360.0))
    with _lock:
        _rwy_list[key] = out
    return out


def _hdg_diff(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def _near_airport(icao: str, lat: float | None, lon: float | None, km: float = 16.0) -> bool:
    if lat is None or lon is None:
        return False
    ap = airport_info(icao)
    if not ap:
        return False
    return _km([ap[2], ap[3]], lat, lon) < km


def resolve_runway(
    icao: str,
    ident: str,
    hdg: float | None = None,
    lat: float | None = None,
    lon: float | None = None,
) -> tuple[str, float, float, float] | None:
    ident = _norm_rwy(ident)
    rwys = list_runways(icao)
    if not ident or not rwys:
        return None
    exact = [row for row in rwys if _same_rwy(row[0], ident)]
    num, suf = _rwy_parts(ident)
    numbered = [row for row in rwys if _rwy_parts(row[0])[0] == num] if num else []
    cands = exact or numbered
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0]
    near = _near_airport(icao, lat, lon)
    if suf:
        tagged = [row for row in cands if _rwy_parts(row[0])[1] == suf]
        if tagged:
            cands = tagged
    if near and hdg is not None:
        return min(cands, key=lambda row: _hdg_diff(hdg, row[3]))
    if near and lat is not None and lon is not None:
        return min(cands, key=lambda row: _km([row[1], row[2]], lat, lon))
    return exact[0] if exact else cands[0]


def _opposite_ident(ident: str) -> str:
    num, suf = _rwy_parts(ident)
    if not num.isdigit():
        return ""
    n = int(num)
    opp = n + 18 if n <= 18 else n - 18
    if opp <= 0:
        opp += 36
    if opp > 36:
        opp -= 36
    flip = {"L": "R", "R": "L"}.get(suf, suf)
    return f"{opp}{flip}"


def runway_pair(
    icao: str,
    ident: str,
    hdg: float | None = None,
    lat: float | None = None,
    lon: float | None = None,
) -> tuple[tuple[str, float, float, float] | None, tuple[str, float, float, float] | None]:
    start = resolve_runway(icao, ident, hdg, lat, lon)
    if not start:
        return None, None
    opp = resolve_runway(icao, _opposite_ident(start[0]))
    if opp and _hdg_diff(start[3], opp[3]) >= 90:
        return start, opp
    best = None
    best_d = 40.0
    for row in list_runways(icao):
        if _same_rwy(row[0], start[0]):
            continue
        d = _hdg_diff(row[3], (start[3] + 180.0) % 360.0)
        if d < best_d:
            best_d = d
            best = row
    return start, best


def runway_end(
    icao: str,
    ident: str,
    hdg: float | None = None,
    lat: float | None = None,
    lon: float | None = None,
) -> tuple[float, float, float] | None:
    icao = (icao or "").strip().upper()
    ident = _norm_rwy(ident)
    if not icao or not ident:
        return None
    stamp = _stamp(icao)
    if stamp is None:
        return None
    near = _near_airport(icao, lat, lon)
    key = (icao, ident, int(hdg or -1) if near else -1, stamp)
    with _lock:
        if key in _rwy_cache:
            return _rwy_cache[key]
    hit_row = resolve_runway(icao, ident, hdg if near else None, lat if near else None, lon if near else None)
    hit = (hit_row[1], hit_row[2], hit_row[3]) if hit_row else None
    with _lock:
        _rwy_cache[key] = hit
    return hit


def _along_nm(lat0: float, lon0: float, hdg: float, lat1: float, lon1: float) -> float:
    dlat = (lat1 - lat0) * 60.0
    dlon = (lon1 - lon0) * 60.0 * math.cos(math.radians(lat0))
    rad = math.radians(hdg)
    return dlat * math.cos(rad) + dlon * math.sin(rad)


def _km2(a: list[float], b: list[float]) -> float:
    return _km(a, b[0], b[1])


def _turn_deg(a: list[float], b: list[float], c: list[float]) -> float:
    def brng(p: list[float], q: list[float]) -> float:
        y = math.sin(math.radians(q[1] - p[1])) * math.cos(math.radians(q[0]))
        x = math.cos(math.radians(p[0])) * math.sin(math.radians(q[0])) - math.sin(
            math.radians(p[0])
        ) * math.cos(math.radians(q[0])) * math.cos(math.radians(q[1] - p[1]))
        return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0

    return abs((brng(b, c) - brng(a, b) + 180.0) % 360.0 - 180.0)


def _sanitize_pts(pts: list[list[float]]) -> list[list[float]]:
    out: list[list[float]] = []
    for pt in pts:
        while len(out) >= 2:
            prev, last = out[-2], out[-1]
            if _km2(last, pt) < 0.04:
                break
            undone = _km2(prev, pt) + 0.12 < _km2(prev, last)
            sharp = _turn_deg(prev, last, pt) > 145.0
            if undone or sharp:
                out.pop()
                continue
            break
        _add_pt(out, pt[0], pt[1])
    return out


def _add_pt(pts: list[list[float]], lat: float | None, lon: float | None) -> None:
    if lat is None or lon is None:
        return
    pt = [round(float(lat), 5), round(float(lon), 5)]
    if pts and abs(pts[-1][0] - pt[0]) < 1e-5 and abs(pts[-1][1] - pt[1]) < 1e-5:
        return
    pts.append(pt)


def _legs_to_pts(legs: list, rwy_pt: tuple[float, float, float] | None) -> list[list[float]]:
    pts: list[list[float]] = []
    fly_to = {"IF", "TF", "CF", "DF", "RF", "AF"}
    heading_codes = {"CA", "VA", "VI", "VM", "FA", "VD"}
    for i, leg in enumerate(legs):
        code = (leg["TrackCode"] or "").upper()
        if code in fly_to and leg["WptLat"] is not None and leg["WptLon"] is not None:
            _add_pt(pts, leg["WptLat"], leg["WptLon"])
            continue
        if code not in heading_codes:
            continue
        dist = None
        try:
            dist = leg["Distance"]
        except Exception:
            dist = None
        if dist is None:
            continue
        if not pts and rwy_pt:
            _add_pt(pts, rwy_pt[0], rwy_pt[1])
        if not pts or leg["Course"] is None:
            continue
        nm = float(dist) if dist is not None else 3.0
        if nm > 80:
            nm = nm / 10.0
        lat, lon = _destination(pts[-1][0], pts[-1][1], float(leg["Course"]), nm)
        _add_pt(pts, lat, lon)
    return pts


def _extend(dst: list[list[float]], src: list[list[float]]) -> None:
    for pt in src:
        _add_pt(dst, pt[0], pt[1])


def _proc_core(name: str) -> tuple[str, str]:
    raw = (name or "").upper().replace(" ", "").replace("-", "")
    raw = raw.replace("0", "O")
    hit = re.fullmatch(r"([A-Z]+)(\d+)([A-Z]?)", raw)
    if hit:
        return hit.group(1), hit.group(3)
    return raw, ""


def _msfs_list(proc: str, icao: str) -> list[dict]:
    data = msfs_procs.airport(icao)
    if not data:
        return []
    return data.get("sids" if proc == "2" else "stars") or []


def _list_proc_rows(proc: str, icao: str) -> list[tuple[str, str]]:
    stamp = _stamp(icao)
    if stamp is None:
        return []
    key = (proc, icao, stamp)
    with _lock:
        if key in _name_cache:
            return _name_cache[key]
    items: list[tuple[str, str]] = []
    for p in _msfs_list(proc, icao):
        name = (p.get("name") or "").strip().upper()
        if not name:
            continue
        rwys = [(r.get("rwy") or "").upper() for r in p.get("rwy") or []]
        for rwy in rwys or [""]:
            items.append((name, rwy))
    with _lock:
        _name_cache[key] = items
    return items


def _spellings(name: str) -> list[str]:
    raw = (name or "").strip().upper().replace(" ", "").replace("-", "")
    out: list[str] = []
    for item in (raw, raw.replace("0", "O"), raw.replace("O", "0")):
        if item and item not in out:
            out.append(item)
    return out


def _lev(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _rwy_rows(rows: list[tuple[str, str]], rwy: str) -> list[tuple[str, str]]:
    if not rwy:
        return rows
    hit = [item for item in rows if item[1] and _same_rwy(item[1], rwy)]
    return hit or rows


def _resolve_proc_name(proc: str, icao: str, name: str, rwy: str = "") -> str:
    want = (name or "").strip().upper()
    if not want:
        return ""
    rows = _rwy_rows(_list_proc_rows(proc, icao), rwy)
    names = [item[0] for item in rows]
    have = set(names)
    for spelling in _spellings(want):
        if spelling in have:
            return spelling
    seen: set[str] = set()
    cores = []
    for spelling in _spellings(want):
        hit = re.fullmatch(r"([A-Z]+)(\d+)([A-Z]?)", spelling)
        if not hit:
            continue
        prefix, ver, suf = hit.group(1), int(hit.group(2)), hit.group(3)
        cores.append((prefix, ver, suf))
        for num in range(ver, 0, -1):
            for pref in _spellings(prefix):
                for body in (f"{pref}{num}{suf}", f"{pref}{num:02d}{suf}"):
                    if body in seen:
                        continue
                    seen.add(body)
                    if body in have:
                        return body
    fuzzy: list[tuple[int, int, str]] = []
    for prefix, ver, suf in cores:
        for item in names:
            ip, isuf = _proc_core(item)
            if suf and isuf and isuf != suf:
                continue
            dist = _lev(ip, prefix)
            if dist <= 1:
                fuzzy.append((dist, -_ver_of(item), item))
    if fuzzy:
        fuzzy.sort()
        return fuzzy[0][2]
    prefixes = {p for p, _, _ in cores}
    family = [item[0] for item in rows if _proc_core(item[0])[0] in prefixes]
    pool = family or names
    if not pool:
        return want
    return random.Random(f"{proc}|{icao}|{want}|{rwy}").choice(sorted(set(pool)))


def _ver_of(name: str) -> int:
    hit = re.fullmatch(r"[A-Z]+(\d+)[A-Z]?", (name or "").upper().replace("0", "O"))
    return int(hit.group(1)) if hit else 0


def _msfs_leg_row(typ: str, trans: str, leg: dict) -> dict:
    code = leg.get("type") or ""
    lat, lon = leg.get("lat"), leg.get("lon")
    if not lat and not lon:
        lat = lon = None
    dist = leg.get("dist")
    return {
        "Type": typ,
        "Transition": trans,
        "TrackCode": code,
        "WptLat": lat,
        "WptLon": lon,
        "Course": leg.get("course"),
        "Distance": dist if dist else None,
    }


def _msfs_groups(proc: str, icao: str, name: str) -> dict[tuple[str, str], list] | None:
    """Map MSFS facility data onto the DFD route-type scheme used by sid_points/star_points.

    SID:  4 = runway transition, 5 = common, 6 = enroute transition.
    STAR: 4 = enroute transition, 5 = common, 6 = runway transition.
    """
    hit = next((p for p in _msfs_list(proc, icao) if (p.get("name") or "").strip().upper() == name), None)
    if hit is None:
        return None
    sid = proc == "2"
    rwy_typ, enr_typ = ("4", "6") if sid else ("6", "4")
    grouped: OrderedDict[tuple[str, str], list] = OrderedDict()
    for tr in hit.get("rwy") or []:
        key = (rwy_typ, (tr.get("rwy") or "").upper())
        grouped.setdefault(key, []).extend(_msfs_leg_row(*key, leg) for leg in tr.get("legs") or [])
    for tr in hit.get("enr") or []:
        key = (enr_typ, (tr.get("name") or "").upper())
        grouped.setdefault(key, []).extend(_msfs_leg_row(*key, leg) for leg in tr.get("legs") or [])
    if hit.get("legs"):
        grouped[("5", "")] = [_msfs_leg_row("5", "", leg) for leg in hit["legs"]]
    return grouped or None


def _load_groups(proc: str, icao: str, name: str, rwy: str = "") -> dict[tuple[str, str], list] | None:
    stamp = _stamp(icao)
    if stamp is None:
        return None
    name = _resolve_proc_name(proc, icao, name, rwy)
    key = (proc, icao, name, stamp)
    with _lock:
        if key in _proc_cache:
            return _proc_cache[key]
    groups = _msfs_groups(proc, icao, name)
    with _lock:
        _proc_cache[key] = groups
    return groups


def _join_branch(
    groups: dict[tuple[str, str], list],
    typ: str,
    from_pt: list[float] | None,
    lat: float | None,
    lon: float | None,
) -> list[list[float]]:
    cands = []
    for k, gl in groups.items():
        if k[0] != typ:
            continue
        br = _legs_to_pts(gl, None)
        if not br:
            continue
        join = _km(br[0], from_pt[0], from_pt[1]) if from_pt else 0.0
        if from_pt and join > 80:
            continue
        acd = _path_km(br, lat, lon) if lat is not None and lon is not None else join
        cands.append((acd, join, br))
    if not cands:
        return []
    return min(cands, key=lambda item: (item[0], item[1]))[2]


def sid_points(
    icao: str,
    sid: str,
    rwy: str,
    lat: float | None = None,
    lon: float | None = None,
    hdg: float | None = None,
) -> list[list[float]]:
    icao = (icao or "").strip().upper()
    sid = (sid or "").strip().upper()
    if not icao or not sid:
        return []
    stamp = _stamp(icao)
    cache_key = (icao, sid, (rwy or "").strip().upper(), stamp)
    with _lock:
        hit = _sid_geom.get(cache_key)
        if hit is not None:
            return [pt[:] for pt in hit]
    resolved = resolve_runway(icao, rwy, hdg, lat, lon)
    start, der = runway_pair(icao, rwy, hdg, lat, lon)
    rwy_ident = (start or resolved)[0] if (start or resolved) else rwy
    groups = _load_groups("2", icao, sid, rwy_ident)
    if not groups:
        return []
    rwy_pt = (start[1], start[2], start[3]) if start else ((resolved[1], resolved[2], resolved[3]) if resolved else None)
    pts: list[list[float]] = []
    if rwy_pt:
        _add_pt(pts, rwy_pt[0], rwy_pt[1])
    if der:
        _add_pt(pts, der[1], der[2])
    for (typ, trans), gl in groups.items():
        if typ != "4":
            continue
        if rwy_ident and not _trans_match(trans, rwy_ident):
            continue
        _extend(pts, _legs_to_pts(gl, rwy_pt))
    common = {"5"}
    if not any(k[0] == "5" for k in groups):
        common.update({"1", "2", "3"})
    for (typ, trans), gl in groups.items():
        if typ in common:
            _extend(pts, _legs_to_pts(gl, rwy_pt))
    _extend(pts, _join_branch(groups, "6", pts[-1] if pts else None, None, None))
    if rwy_pt and der and len(pts) >= 3:
        along = _along_nm(der[1], der[2], rwy_pt[2], pts[2][0], pts[2][1])
        if along < 1.4:
            ext = _destination(der[1], der[2], rwy_pt[2], 2.0)
            pts.insert(2, [round(ext[0], 5), round(ext[1], 5)])
    pts = _sanitize_pts(pts)
    if stamp is not None:
        with _lock:
            _sid_geom[cache_key] = [pt[:] for pt in pts]
    return pts


def star_points(
    icao: str,
    star: str,
    rwy: str,
    lat: float | None = None,
    lon: float | None = None,
    hdg: float | None = None,
) -> list[list[float]]:
    icao = (icao or "").strip().upper()
    star = (star or "").strip().upper()
    if not icao or not star:
        return []
    resolved = resolve_runway(icao, rwy, hdg, lat, lon)
    rwy_ident = resolved[0] if resolved else rwy
    groups = _load_groups("1", icao, star, rwy_ident)
    if not groups:
        return []
    pts: list[list[float]] = []
    common = {"5"}
    if not any(k[0] == "5" for k in groups):
        common.update({"1", "2", "3"})
    for (typ, trans), gl in groups.items():
        if typ in common:
            _extend(pts, _legs_to_pts(gl, None))
    for (typ, trans), gl in groups.items():
        if typ != "6":
            continue
        if rwy_ident and not _trans_match(trans, rwy_ident):
            continue
        _extend(pts, _legs_to_pts(gl, None))
    pts = _sanitize_pts(pts)
    if resolved and pts and _km(pts[-1], resolved[1], resolved[2]) > 6:
        _add_pt(pts, resolved[1], resolved[2])
    return pts


def warmup() -> None:
    msfs_procs.start()
