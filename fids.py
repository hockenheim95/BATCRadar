"""Departure / arrival boards for the player's origin and destination.

Moved out of the browser so every tablet / phone sees the same board and the
latch / hold-back state survives a page reload.  Rows are aircraft on that field
or in takeoff / approach; the client shows the board for the selected aircraft's
current airport.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone

from planes import _haversine_field

FIDS_KEEP = 30
DEP_GONE_MS = 5 * 60 * 1000
ARR_LATCH_MAX_MS = 6 * 3600 * 1000
PARKED_DEP_WINDOW_MS = 30 * 60 * 1000
PARKED_DEP_BYPASS_MAX = 20
ARR_ETA_WINDOW_MS = 30 * 60 * 1000
ARR_ON_TIME_MAX_DELAY_MS = 5 * 60 * 1000
ARR_DELAY_ROUND_MS = 5 * 60 * 1000
ARR_ETA_MIN_GS_KT = 60.0
ARR_STILL_MS = 25 * 1000
ARR_STILL_M = 15.0
FAR_MS = 8.64e7

_BATC_Z = re.compile(r"([A-Za-z]{3})\s+(\d{1,2}):(\d{2})Z")
_BATC_ANY = re.compile(r"([A-Za-z]{3}|\d{1,2})\s+(\d{1,2}):(\d{2})Z")
_DAYS = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}
_WD_NAMES = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
_GROUND_FREQ = re.compile(r"\bground\b|\bboden\b")


def _parse_batc_z_weekday(text: str | None, now_ms: float) -> float | None:
    """'Thu 14:35Z' -> epoch ms on the nearest matching weekday (AI / default)."""
    m = _BATC_Z.search(text or "")
    if not m:
        return None
    now = datetime.fromtimestamp(now_ms / 1000.0, tz=timezone.utc)
    d = now.replace(hour=int(m.group(2)), minute=int(m.group(3)), second=0, microsecond=0)
    want = _DAYS.get(m.group(1).lower())
    if want is None:
        return d.timestamp() * 1000.0
    js_day = (d.weekday() + 1) % 7
    diff = want - js_day
    if diff > 3:
        diff -= 7
    if diff < -3:
        diff += 7
    d = d + timedelta(days=diff)
    return d.timestamp() * 1000.0


def _parse_batc_z_player(text: str | None, now_ms: float) -> float | None:
    """Player: clock nearest now (today ±1 day); BATC weekday is often the published schedule."""
    m = _BATC_Z.search(text or "")
    if not m:
        return None
    now = datetime.fromtimestamp(now_ms / 1000.0, tz=timezone.utc)
    today = now.replace(hour=int(m.group(2)), minute=int(m.group(3)), second=0, microsecond=0)
    candidates = [today + timedelta(days=delta) for delta in (-1, 0, 1)]
    want = _DAYS.get(m.group(1).lower())
    if want is not None:
        js_day = (today.weekday() + 1) % 7
        diff = want - js_day
        if diff > 3:
            diff -= 7
        if diff < -3:
            diff += 7
        candidates.append(today + timedelta(days=diff))
    best = min(candidates, key=lambda d: abs(d.timestamp() * 1000.0 - now_ms))
    return best.timestamp() * 1000.0


def _is_player_ac(ac: dict, own_id: str | None) -> bool:
    return bool(ac.get("isOwnship") or (own_id is not None and ac.get("id") == own_id) or _state(ac) == "player")


def parse_batc_z(text: str | None, now_ms: float, *, player: bool = False) -> float | None:
    if player:
        return _parse_batc_z_player(text, now_ms)
    return _parse_batc_z_weekday(text, now_ms)


def _batc_token_clock(text: str | None) -> tuple[str, int, int] | None:
    m = _BATC_ANY.search(text or "")
    if not m:
        return None
    return m.group(1), int(m.group(2)), int(m.group(3))


def _fmt_batc_weekday(dt: datetime) -> str:
    js_day = (dt.weekday() + 1) % 7
    return f"{_WD_NAMES[js_day]} {dt.hour:02d}:{dt.minute:02d}Z"


def _sched_day_shift(dep_tok: str, arr_tok: str, dep_hm: tuple[int, int], arr_hm: tuple[int, int]) -> int:
    """Days to add to arrival vs departure (0 or 1), including month wrap 31→1."""
    if dep_tok.isdigit() and arr_tok.isdigit():
        delta = int(arr_tok) - int(dep_tok)
        if delta < -15:
            return 1
        if delta > 15:
            return 0
        if delta >= 1:
            return 1
        return 1 if arr_hm <= dep_hm else 0
    dep_wd = _DAYS.get(dep_tok.lower())
    arr_wd = _DAYS.get(arr_tok.lower())
    if dep_wd is not None and arr_wd is not None:
        delta = (arr_wd - dep_wd) % 7
        if delta == 0:
            return 1 if arr_hm <= dep_hm else 0
        return 1 if delta == 1 else (1 if arr_hm <= dep_hm else 0)
    return 1 if arr_hm <= dep_hm else 0


def rewrite_ownship_batc_times(
    time_from: str | None, time_to: str | None, now_ms: float | None = None
) -> tuple[str, str]:
    """Rewrite player STD/STA to AI form 'Fri 07:55Z', keeping overnight on the next weekday."""
    raw_from = str(time_from or "").strip()
    raw_to = str(time_to or "").strip()
    if now_ms is None:
        now_ms = time.time() * 1000.0
    now = datetime.fromtimestamp(now_ms / 1000.0, tz=timezone.utc)
    today = now.replace(second=0, microsecond=0)
    dep_p = _batc_token_clock(raw_from)
    arr_p = _batc_token_clock(raw_to)

    def at_clock(day: datetime, hour: int, minute: int) -> datetime:
        return day.replace(hour=hour, minute=minute, second=0, microsecond=0)

    dep_dt = at_clock(today, dep_p[1], dep_p[2]) if dep_p else None
    arr_dt = at_clock(today, arr_p[1], arr_p[2]) if arr_p else None
    if dep_dt is not None and arr_dt is not None and dep_p and arr_p:
        shift = _sched_day_shift(dep_p[0], arr_p[0], (dep_p[1], dep_p[2]), (arr_p[1], arr_p[2]))
        if shift:
            arr_dt = at_clock(today + timedelta(days=shift), arr_p[1], arr_p[2])
        elif arr_dt <= dep_dt:
            arr_dt = arr_dt + timedelta(days=1)
    return (
        _fmt_batc_weekday(dep_dt) if dep_dt is not None else raw_from,
        _fmt_batc_weekday(arr_dt) if arr_dt is not None else raw_to,
    )


def _km(a_lat, a_lon, b_lat, b_lon) -> float:
    try:
        return _haversine_field(float(a_lat), float(a_lon), float(b_lat), float(b_lon))
    except (TypeError, ValueError):
        return 0.0


def _heading_delta(a, b) -> float:
    d = abs((float(a or 0)) - (float(b or 0))) % 360
    return 360 - d if d > 180 else d


def _state(ac: dict) -> str:
    return str(ac.get("state") or "").lower()


def _grounded_state(ac: dict) -> bool:
    return bool(ac.get("onGround")) or _state(ac) in ("parked", "taxi", "pushback", "player")


def _place_coords(ac: dict, kind: str) -> tuple[float, float] | None:
    lat = ac.get(f"{kind}Lat")
    lon = ac.get(f"{kind}Lon")
    if lat is None or lon is None:
        return None
    try:
        return float(lat), float(lon)
    except (TypeError, ValueError):
        return None


def _at_dest_field(ac: dict) -> bool:
    coords = _place_coords(ac, "dest")
    if not coords:
        return False
    if not ac.get("onGround") and float(ac.get("gs") or 0) > 40:
        return False
    try:
        return _km(ac["lat"], ac["lon"], coords[0], coords[1]) < 8.0
    except (KeyError, TypeError, ValueError):
        return False


def _at_place(ac: dict, kind: str, km: float = 8.0) -> bool:
    coords = _place_coords(ac, kind)
    if not coords:
        return False
    try:
        return _km(ac["lat"], ac["lon"], coords[0], coords[1]) < km
    except (TypeError, ValueError, KeyError):
        return False


def _is_approaching(ac: dict) -> bool:
    if ac.get("onGround"):
        return False
    if ac.get("atDestination"):
        return True
    st = _state(ac)
    if st == "approach":
        return True
    if re.search(r"on approach|cleared to land|landing", str(ac.get("status") or ""), re.I):
        return True
    coords = _place_coords(ac, "dest")
    if not coords:
        return False
    try:
        dist = _km(ac["lat"], ac["lon"], coords[0], coords[1])
    except (TypeError, ValueError, KeyError):
        return False
    return dist < 55.0 and float(ac.get("alt") or 0) < 14000


def on_field_or_term(ac: dict, ap: str, own: dict | None = None) -> bool:
    """On this airport by flight plan (origin/dest), taking off from it, or inbound to it."""
    ap = ap.upper()
    origin = str(ac.get("origin") or "").upper()
    dest = str(ac.get("dest") or "").upper()
    st = _state(ac)
    if origin == ap and st == "takeoff":
        return True
    if dest == ap and operates_arr(ac, ap):
        return True
    grounded = _grounded_state(ac) or st == "takeoff" or (
        float(ac.get("gs") or 0) < 40 and float(ac.get("alt") or 0) < 2500
    )
    if not grounded:
        return False
    if origin == ap and _at_place(ac, "origin"):
        return True
    if dest == ap and _at_place(ac, "dest"):
        return True
    return False


def operates_dep(ac: dict, ap: str) -> bool:
    """Flight plan departs from ap and the aircraft is at or has left that airport."""
    ap = ap.upper()
    if str(ac.get("origin") or "").upper() != ap:
        return False
    if operates_arr(ac, ap) and not (_grounded_state(ac) and _at_place(ac, "origin")):
        return False
    st = _state(ac)
    if st in ("takeoff", "flight"):
        return True
    if _at_place(ac, "origin") and _grounded_state(ac):
        return True
    dest = str(ac.get("dest") or "").upper()
    airborne = not ac.get("onGround") and st not in ("parked", "taxi", "pushback", "player")
    return bool(airborne and dest and dest != ap and float(ac.get("gs") or 0) > 40)


def operates_arr(ac: dict, ap: str) -> bool:
    """Flight plan arrives at ap and the aircraft is inbound or at that airport (not still at origin)."""
    ap = ap.upper()
    if str(ac.get("dest") or "").upper() != ap:
        return False
    origin = str(ac.get("origin") or "").upper()
    st = _state(ac)
    if _grounded_state(ac) and origin and origin != ap and _at_place(ac, "origin"):
        return False
    airborne = not ac.get("onGround") and st not in ("parked", "taxi", "pushback", "player")
    if airborne or ac.get("atDestination"):
        return True
    if _at_place(ac, "dest"):
        return True
    return False


def fids_board_eligible(ac: dict, kind: str, now: float, own_id: str | None = None) -> bool:
    """Scheduled airline FIDS rows only (BATC STD/STA); skip same-field / missing times."""
    origin = str(ac.get("origin") or "").upper()
    dest = str(ac.get("dest") or "").upper()
    if not origin or not dest or origin == dest:
        return False
    player = _is_player_ac(ac, own_id)
    if kind == "dep":
        return parse_batc_z(ac.get("timeFrom"), now, player=player) is not None
    return parse_batc_z(ac.get("timeTo"), now, player=player) is not None


def scheduled_inbound(ac: dict, ap: str) -> bool:
    """Dest is ap but the aircraft has not left its origin yet (arrival board: Scheduled)."""
    ap = ap.upper()
    if str(ac.get("dest") or "").upper() != ap:
        return False
    origin = str(ac.get("origin") or "").upper()
    if not origin or origin == ap:
        return False
    if not _grounded_state(ac):
        return False
    return _at_place(ac, "origin")


def _estimate_arr_eta_ms(ac: dict, now: float) -> float | None:
    coords = _place_coords(ac, "dest")
    if not coords:
        return None
    try:
        lat, lon = float(ac["lat"]), float(ac["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    remain_km = _km(lat, lon, coords[0], coords[1])
    gs = float(ac.get("gs") or 0)
    if gs < ARR_ETA_MIN_GS_KT:
        return None
    speed_kmh = gs * 1.852
    if speed_kmh < 1:
        return None
    ete_ms = (remain_km / speed_kmh) * 3600 * 1000
    return now + ete_ms


def _arr_final(ac: dict) -> bool:
    raw = str(ac.get("status") or "")
    return bool(re.search(r"cleared to land|final approach|\blanding\b", raw, re.I))


def _going_around(ac: dict) -> bool:
    return bool(re.search(r"\bgoing\s+around\b", str(ac.get("status") or ""), re.I))


def _at_arr_ap(ac: dict, ap: str) -> bool:
    ap = ap.upper()
    if str(ac.get("dest") or "").upper() != ap:
        return False
    if ac.get("atDestination"):
        return True
    return _at_dest_field(ac)


_ARR_POST_LAND = ("LANDED", "TAXI", "ARRIVED")


def _arr_delay_minutes(ac: dict, now: float) -> int | None:
    """Minutes late vs STA from ETA; None if unknown or within on-time window."""
    st = _state(ac)
    airborne = not ac.get("onGround") and st not in ("parked", "taxi", "pushback", "player")
    if not airborne:
        return None
    eta = _estimate_arr_eta_ms(ac, now)
    if eta is None or eta - now > ARR_ETA_WINDOW_MS:
        return None
    sta = parse_batc_z(ac.get("timeTo"), now, player=_is_player_ac(ac, None))
    if sta is None:
        return None
    delay_ms = eta - sta
    if delay_ms <= ARR_ON_TIME_MAX_DELAY_MS:
        return None
    return int(round(delay_ms / ARR_DELAY_ROUND_MS) * (ARR_DELAY_ROUND_MS / 60000))


def _arr_eta_delay_status(ac: dict, now: float) -> tuple[str, str] | None:
    """Airborne, ETA within 30 min: ON TIME (<=5 min late) or DELAYED (5-min steps)."""
    delay_min = _arr_delay_minutes(ac, now)
    if delay_min is not None:
        return "DELAYED", "delay"
    st = _state(ac)
    airborne = not ac.get("onGround") and st not in ("parked", "taxi", "pushback", "player")
    if not airborne:
        return None
    eta = _estimate_arr_eta_ms(ac, now)
    if eta is None or eta - now > ARR_ETA_WINDOW_MS:
        return None
    sta = parse_batc_z(ac.get("timeTo"), now, player=_is_player_ac(ac, None))
    if sta is None:
        return None
    if eta - sta <= ARR_ON_TIME_MAX_DELAY_MS:
        return "ON TIME", "ontime"
    return None


def _inbound(label: str) -> bool:
    return label in (
        "LANDING",
        "GO AROUND",
        "EN ROUTE",
        "Delayed",
        "ON TIME",
        "DELAYED",
    )


def _arr_time_key(r: dict, now: float) -> float:
    t = r.get("t")
    return t if t is not None else now + FAR_MS


class Fids:
    def __init__(self) -> None:
        self._stand: dict[str, tuple[float, float, float]] = {}
        self._departed_at: dict[str, float] = {}
        self._seen_before_departure: set[str] = set()
        self._arr_latch: dict[str, dict[str, dict]] = {}
        self._arr_tracked: dict[str, dict[str, dict]] = {}
        self._arr_board_joined: dict[str, dict[str, float]] = {}
        self._arr_goaround: set[str] = set()
        self._arr_still: dict[str, tuple[float, float, float]] = {}
        self._last_own: tuple[float, float] | None = None
        self._dep_len_by_ap: dict[str, int] = {}

    def clear_boards(self) -> None:
        """No BATC traffic — drop departures/arrivals latch state (boards show empty)."""
        self._stand.clear()
        self._departed_at.clear()
        self._seen_before_departure.clear()
        self._arr_latch.clear()
        self._arr_tracked.clear()
        self._arr_board_joined.clear()
        self._arr_goaround.clear()
        self._arr_still.clear()
        self._dep_len_by_ap.clear()

    def _arr_term_ok(self, ac: dict, ap: str, own: dict | None) -> bool:
        """Any flight operating into ap; keep go-around / prior board ids briefly after pool gaps."""
        ap = ap.upper()
        if str(ac.get("dest") or "").upper() != ap:
            return False
        if operates_arr(ac, ap):
            return True
        aid = ac.get("id")
        if not aid:
            return False
        if _going_around(ac) or aid in self._arr_goaround:
            return True
        return aid in self._arr_board_joined.get(ap, {})

    def _arr_pin_ids(self, rows: list[dict], ap: str) -> set[str]:
        """Inbound / latched / go-around rows must not lose a cap slot to a newer scheduled row."""
        pinned: set[str] = set()
        for r in rows:
            aid = r.get("id")
            if not aid:
                continue
            label = r.get("label")
            if not label:
                st = r.get("st")
                if isinstance(st, list) and st:
                    label = st[0]
            if r.get("latched") or (label and _inbound(label)) or label in _ARR_POST_LAND:
                pinned.add(str(aid))
        pinned.update(self._arr_goaround)
        pinned.update(self._arr_latch.get(ap, {}).keys())
        return pinned

    # -- per-plane helpers -------------------------------------------------

    def map_visible(self, ac: dict, now: float, own: dict | None = None, own_id: str | None = None, *, dep_bypass: bool = True) -> bool:
        if ac.get("isOwnship"):
            return True
        if dep_bypass and self._fids_dep_bypass(ac, now, own, own_id):
            return True
        if _state(ac) != "parked":
            return True
        std = parse_batc_z(ac.get("timeFrom"), now)
        if std is not None:
            until = std - now
            if 0 <= until <= PARKED_DEP_WINDOW_MS:
                return True
        sta = parse_batc_z(ac.get("timeTo"), now)
        if sta is not None and (ac.get("atDestination") or _at_dest_field(ac)):
            since = now - sta
            if 0 <= since <= PARKED_DEP_WINDOW_MS:
                return True
        return False

    def _fids_dep_bypass(self, ac: dict, now: float, own: dict | None, own_id: str | None) -> bool:
        """Parked FIDS departures stay on the map only when that board has fewer than 20 rows."""
        if not own:
            return False
        st = _state(ac)
        for key in ("origin", "dest"):
            ap = str(own.get(key) or "").upper()
            if not ap:
                continue
            if self._dep_len_by_ap.get(ap, 0) >= PARKED_DEP_BYPASS_MAX:
                continue
            if not operates_dep(ac, ap):
                continue
            if not fids_board_eligible(ac, "dep", now, own_id):
                continue
            if not on_field_or_term(ac, ap, own) and st not in ("takeoff", "flight"):
                continue
            t = parse_batc_z(ac.get("timeFrom"), now, player=_is_player_ac(ac, own_id))
            if t is not None:
                is_own = ac.get("id") == own_id or ac.get("isOwnship")
                if (t < now - 6 * 3600 * 1000 or t > now + 18 * 3600 * 1000) and not is_own:
                    continue
            return True
        return False

    def _note_stand(self, ac: dict) -> None:
        aid = ac.get("id")
        if not aid:
            return
        st = _state(ac)
        rolling = st in ("taxi", "pushback", "takeoff", "flight")
        flying = not ac.get("onGround") and st not in ("parked", "player")
        if rolling or flying or ac.get("atDestination"):
            self._stand.pop(aid, None)
            return
        if aid not in self._stand:
            self._stand[aid] = (ac.get("lat"), ac.get("lon"), ac.get("hdg"))

    def _arr_done(self, ac: dict, own_id: str | None, now: float) -> bool:
        """True once an arrival has finished (parked, despawned, or frozen at dest)."""
        if not ac or _is_player_ac(ac, own_id):
            return False
        st = _state(ac)
        aid = ac.get("id")
        grounded = bool(ac.get("onGround")) or st in ("taxi", "parked", "pushback")
        at_dest = bool(ac.get("atDestination") or _at_dest_field(ac))
        if not grounded or not at_dest:
            if aid:
                self._arr_still.pop(aid, None)
            return False
        if st == "parked":
            return True
        if ac.get("inSim") is False:
            return True
        lat, lon = ac.get("lat"), ac.get("lon")
        prev = self._arr_still.get(aid) if aid else None
        moved = prev is None or _km(prev[0], prev[1], lat, lon) * 1000 >= ARR_STILL_M
        if aid:
            if moved:
                self._arr_still[aid] = (lat, lon, now)
                return False
            if now - prev[2] >= ARR_STILL_MS:
                return True
        return False

    def _is_pushing(self, ac: dict, own_id: str | None) -> bool:
        if not ac or ac.get("atDestination"):
            return False
        st = _state(ac)
        if st in ("taxi", "pushback", "takeoff", "flight"):
            return False
        is_own = bool(ac.get("isOwnship") or (own_id is not None and ac.get("id") == own_id) or st == "player")
        if re.search("push", str(ac.get("status") or ""), re.I):
            return True
        if is_own and float(ac.get("gs") or 0) >= 2:
            return True
        freq = f"{ac.get('freqName') or ''} {ac.get('freq') or ''}".lower()
        if is_own and _GROUND_FREQ.search(freq) and "unicom" not in freq:
            return True
        origin = self._stand.get(ac.get("id"))
        if not origin:
            return False
        moved_m = _km(origin[0], origin[1], ac.get("lat"), ac.get("lon")) * 1000
        return moved_m > 12 or _heading_delta(origin[2], ac.get("hdg")) > 18

    def status(self, ac: dict, kind: str, ap: str, own_id: str | None, now: float) -> tuple[str, str]:
        st = _state(ac)
        airborne = not ac.get("onGround") and st not in ("parked", "taxi", "pushback", "player")
        if kind == "dep":
            if st == "taxi":
                return "Taxi", "taxi"
            if st == "pushback" or self._is_pushing(ac, own_id):
                return "Pushback", "taxi"
            if st == "takeoff":
                return "Take off", "air"
            if st == "flight" or airborne:
                return "Departed", "gone"
            std = parse_batc_z(ac.get("timeFrom"), now, player=_is_player_ac(ac, own_id))
            if std is not None:
                late = now - std
                if late > 61 * 60 * 1000 and (own_id is None or ac.get("id") != own_id):
                    return "Cancelled", "cancel"
                if late > 5 * 60 * 1000:
                    return "Delayed", "delay"
                if late >= -20 * 60 * 1000:
                    return "Boarding", "board"
            return "Scheduled", "sched"
        raw_status = str(ac.get("status") or "")
        at_arr = _at_arr_ap(ac, ap)
        on_arr_gnd = at_arr and (
            ac.get("onGround") or bool(re.search(r"\bvacat", raw_status, re.I))
        )
        if on_arr_gnd:
            aid = ac.get("id")
            if aid:
                self._arr_goaround.discard(aid)
            if self._arr_done(ac, own_id, now):
                return "ARRIVED", "arrived"
            if st == "taxi":
                return "TAXI", "taxi"
            if st == "parked":
                return "ARRIVED", "arrived"
            return "LANDED", "landed"
        in_flight = airborne or (st in ("flight", "takeoff") and not ac.get("onGround"))
        if in_flight:
            aid = ac.get("id")
            if aid and _going_around(ac):
                self._arr_goaround.add(aid)
                return "GO AROUND", "goaround"
            if aid and aid in self._arr_goaround:
                return "GO AROUND", "goaround"
            if _arr_final(ac):
                return "LANDING", "land"
            eta_st = _arr_eta_delay_status(ac, now)
            if eta_st:
                return eta_st
            return "EN ROUTE", "air"
        if at_arr:
            if self._arr_done(ac, own_id, now) or st != "taxi":
                return "ARRIVED", "arrived"
            return "TAXI", "taxi"
        return "Scheduled", "sched"

    # -- boards ------------------------------------------------------------

    def _pick_dep(self, pool: list[dict], ap: str, own_id, now: float, own: dict | None = None) -> list[dict]:
        rows = []
        for ac in pool:
            if not operates_dep(ac, ap):
                continue
            if not fids_board_eligible(ac, "dep", now, own_id):
                continue
            if not on_field_or_term(ac, ap, own) and _state(ac) not in ("takeoff", "flight"):
                continue
            rows.append(
                {
                    "ac": ac,
                    "t": parse_batc_z(ac.get("timeFrom"), now, player=_is_player_ac(ac, own_id)),
                    "gnd": _grounded_state(ac),
                }
            )
        rows.sort(key=lambda r: r["t"] if r["t"] is not None else now + FAR_MS)
        past = 6 * 3600 * 1000
        keep = []
        for r in rows:
            t = r["t"]
            is_own = r["ac"].get("id") == own_id or r["ac"].get("isOwnship")
            if t is not None and (t < now - past or t > now + 18 * 3600 * 1000) and not is_own:
                continue
            keep.append(r)
        out = keep if keep else rows
        result = []
        for r in out:
            aid = r["ac"]["id"]
            label, cls = self.status(r["ac"], "dep", ap, own_id, now)
            if label == "Delayed" and not self.map_visible(r["ac"], now, own, own_id, dep_bypass=False):
                continue
            if label != "Take off":
                if label != "Departed":
                    self._seen_before_departure.add(aid)
                    if label in ("Taxi", "Pushback", "Boarding"):
                        self._departed_at.pop(aid, None)
                else:
                    if aid not in self._departed_at:
                        long_gone = r["t"] is not None and now - r["t"] > 30 * 60 * 1000
                        already_gone = aid not in self._seen_before_departure
                        self._departed_at[aid] = now - DEP_GONE_MS if (long_gone or already_gone) else now
                    if not now - self._departed_at[aid] < DEP_GONE_MS:
                        continue
            result.append({"id": aid, "st": [label, cls]})
        return result[:FIDS_KEEP]

    def _cap_arr_board(self, rows: list[dict], ap: str, now: float, cap: int) -> list[dict]:
        """Up to cap rows; new ids join, oldest non-inbound board tenure leaves; display sorted by STA."""
        joined = self._arr_board_joined.setdefault(ap, {})
        by_id = {r["id"]: r for r in rows}
        pinned = self._arr_pin_ids(rows, ap)

        for aid in list(joined):
            if aid not in by_id:
                del joined[aid]

        for aid in by_id:
            if aid in joined:
                continue
            joined[aid] = now
            while len(joined) > cap:
                drop_candidates = [x for x in joined if x not in pinned]
                if not drop_candidates:
                    break
                drop = min(drop_candidates, key=joined.get)
                del joined[drop]

        kept = [by_id[aid] for aid in joined if aid in by_id]
        kept.sort(key=lambda r: _arr_time_key(r, now))
        return kept

    def _pick_arr(self, pool: list[dict], ap: str, own_id, now: float, own: dict | None = None) -> list[dict]:
        latch = self._arr_latch.setdefault(ap, {})
        tracked = self._arr_tracked.setdefault(ap, {})
        by_id = {ac["id"]: ac for ac in pool}
        past = 40 * 60 * 1000

        def t_of(ac):
            return parse_batc_z(ac.get("timeTo"), now, player=_is_player_ac(ac, own_id))

        for aid in list(latch):
            entry = latch[aid]
            fresh = by_id.get(aid)
            ac = fresh if fresh else entry["ac"]
            if not self.map_visible(ac, now, own, own_id):
                del latch[aid]
                continue
            t = t_of(entry["ac"])
            if t is not None and t < now - ARR_LATCH_MAX_MS:
                del latch[aid]

        def do_latch(ac):
            if not self.map_visible(ac, now, own, own_id):
                return
            prev = latch.get(ac["id"])
            latch[ac["id"]] = {"ac": dict(ac), "at": prev["at"] if prev else now}

        for ac in pool:
            if not operates_arr(ac, ap):
                continue
            if not fids_board_eligible(ac, "arr", now, own_id):
                continue
            if not self._arr_term_ok(ac, ap, own):
                continue
            label = self.status(ac, "arr", ap, own_id, now)[0]
            if label in _ARR_POST_LAND:
                do_latch(ac)
                tracked.pop(ac["id"], None)
                continue
            if _inbound(label):
                tracked[ac["id"]] = {"ac": dict(ac), "t": t_of(ac)}

        for aid in list(tracked):
            entry = tracked[aid]
            if aid in by_id or aid in latch:
                continue
            t = entry["t"]
            if t is not None and t < now - 2 * 60 * 1000:
                do_latch(entry["ac"])
                tracked.pop(aid, None)

        rows = []
        active_ids = set()
        for ac in pool:
            if not fids_board_eligible(ac, "arr", now, own_id):
                continue
            if not operates_arr(ac, ap):
                continue
            if not self._arr_term_ok(ac, ap, own):
                continue
            label, cls = self.status(ac, "arr", ap, own_id, now)
            if label in _ARR_POST_LAND:
                continue
            t = t_of(ac)
            inbound = _inbound(label)
            is_own = ac.get("id") == own_id or ac.get("isOwnship")
            if not inbound and not self.map_visible(ac, now, own, own_id):
                continue
            if t is None:
                if _grounded_state(ac) and not on_field_or_term(ac, ap, own):
                    continue
            elif not is_own:
                if t < now - past and not inbound:
                    continue
                if t > now + 18 * 3600 * 1000:
                    continue
            active_ids.add(ac["id"])
            row = {"id": ac["id"], "t": t, "latched": False, "label": label, "st": [label, cls]}
            late = _arr_delay_minutes(ac, now)
            if late is not None:
                row["lateMin"] = late
            rows.append(row)

        for aid, entry in list(latch.items()):
            if aid in active_ids:
                continue
            ac = dict(entry["ac"])
            fresh = by_id.get(aid)
            if fresh:
                ac["lat"] = fresh.get("lat")
                ac["lon"] = fresh.get("lon")
                if fresh.get("gate") is not None:
                    ac["gate"] = fresh["gate"]
                ac["onGround"] = fresh.get("onGround")
                ac["gs"] = fresh.get("gs")
                ac["hdg"] = fresh.get("hdg")
                if fresh.get("state") is not None:
                    ac["state"] = fresh["state"]
                if fresh.get("status") is not None:
                    ac["status"] = fresh["status"]
                if "atDestination" in fresh:
                    ac["atDestination"] = fresh["atDestination"]
            status_ac = fresh if fresh else ac
            if str(status_ac.get("dest") or "").upper() != ap:
                del latch[aid]
                continue
            if not fids_board_eligible(status_ac, "arr", now, own_id):
                del latch[aid]
                continue
            if not self.map_visible(status_ac, now, own, own_id):
                del latch[aid]
                continue
            label, cls = self.status(status_ac, "arr", ap, own_id, now)
            # BATC AI often despawns at the gate while still "taxi"; keep the row as ARRIVED.
            if fresh is None and label in ("TAXI", "LANDED"):
                label, cls = "ARRIVED", "arrived"
            if not self._arr_term_ok(status_ac, ap, own) and label not in _ARR_POST_LAND:
                continue
            t = t_of(entry["ac"])
            latched_row = {
                "id": aid,
                "t": t if t is not None else entry["at"],
                "latched": True,
                "label": label,
                "st": [label, cls],
                "ac": ac,
            }
            late = _arr_delay_minutes(status_ac, now)
            if late is not None:
                latched_row["lateMin"] = late
            rows.append(latched_row)

        rows = self._cap_arr_board(rows, ap, now, FIDS_KEEP)
        out = []
        for r in rows:
            item = {"id": r["id"], "st": r["st"], "latched": r["latched"]}
            if r.get("lateMin") is not None:
                item["lateMin"] = r["lateMin"]
            if r["latched"]:
                item["ac"] = r["ac"]
            out.append(item)
        return out

    # -- entry point -------------------------------------------------------

    def update(self, fleet: list[dict], own: dict | None) -> dict:
        now = time.time() * 1000.0
        own_id = own.get("id") if own else None

        if own and self._last_own is not None:
            if _km(self._last_own[0], self._last_own[1], own["lat"], own["lon"]) > 80:
                self._stand.clear()
                self._arr_still.clear()
        self._last_own = (own["lat"], own["lon"]) if own else None

        aps: list[str] = []
        dep_boards: dict[str, list] = {}
        self._dep_len_by_ap = {}
        if own:
            for key in ("origin", "dest"):
                code = str(own.get(key) or "").upper()
                if code and code not in aps:
                    aps.append(code)
            for ap in aps:
                dep_boards[ap] = self._pick_dep(fleet, ap, own_id, now, own)
                self._dep_len_by_ap[ap] = len(dep_boards[ap])

        visible_ids = set()
        fleet_ids = set()
        for ac in fleet:
            aid = ac.get("id")
            if aid:
                fleet_ids.add(aid)
            if self.map_visible(ac, now, own, own_id):
                visible_ids.add(ac["id"])
                self._note_stand(ac)
        self._arr_goaround &= fleet_ids
        for aid in list(self._stand):
            if aid not in visible_ids:
                del self._stand[aid]
        for aid in list(self._arr_still):
            if aid not in fleet_ids:
                del self._arr_still[aid]

        if not own:
            self._arr_latch.clear()
            self._arr_tracked.clear()
            self._arr_board_joined.clear()
            return {}
        for stale in [a for a in self._arr_latch if a not in aps]:
            del self._arr_latch[stale]
        for stale in [a for a in self._arr_tracked if a not in aps]:
            del self._arr_tracked[stale]
        for stale in [a for a in self._arr_board_joined if a not in aps]:
            del self._arr_board_joined[stale]
        return {
            ap: {
                "dep": dep_boards[ap],
                "arr": self._pick_arr(fleet, ap, own_id, now, own),
            }
            for ap in aps
        }
