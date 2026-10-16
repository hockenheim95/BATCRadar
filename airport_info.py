"""Airport panel payload: METAR and BATC-published in-use runways."""

from __future__ import annotations

import math
import re

from navdata import list_runways, _norm_rwy as _nav_norm_rwy, _same_rwy


def _parse_metar(metar: str) -> dict:
    text = (metar or "").upper()
    out: dict = {}
    m = re.search(r"\b(\d{3}|VRB)(\d{2,3})(?:G\d{2,3})?KT\b", text)
    if m:
        out["windFrom"] = None if m.group(1) == "VRB" else float(m.group(1))
        out["windKt"] = float(m.group(2))
    q = re.search(r"\bQ(\d{4})\b", text)
    if q:
        out["qnh"] = float(q.group(1))
    a = re.search(r"\bA(\d{4})\b", text)
    if a and "qnh" not in out:
        out["qnh"] = round(float(a.group(1)) * 0.3386, 1)
    t = re.search(r"\s(M?\d{2})/(M?\d{2})\b", text)
    if t:
        def n(v: str) -> float:
            return -float(v[1:]) if v.startswith("M") else float(v)
        out["tempC"] = n(t.group(1))
        out["dewC"] = n(t.group(2))
    if "CAVOK" in text:
        out["visM"] = 9999.0
    v = re.search(r"\b(\d{4})\b", text)
    if v and "visM" not in out and v.group(1) not in (q.group(1) if q else "",):
        vis = float(v.group(1))
        if vis <= 9999:
            out["visM"] = vis
    return out


def _wx_num(val) -> float | None:
    if val is None:
        return None
    try:
        n = float(val)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def _synthetic_metar(icao: str, wx: dict) -> str:
    code = (icao or "ZZZZ").upper()[:4]
    wind_from = _wx_num(wx.get("windFrom"))
    wind_kt = _wx_num(wx.get("windKt"))
    vis_m = _wx_num(wx.get("visM"))
    temp = _wx_num(wx.get("tempC"))
    dew = _wx_num(wx.get("dewC"))
    qnh = _wx_num(wx.get("qnh"))
    bits = [code]
    if wind_from is not None and wind_kt is not None:
        bits.append(f"{int(wind_from) % 360:03d}{int(round(wind_kt)):02d}KT")
    elif wind_kt is not None:
        bits.append(f"VRB{int(round(wind_kt)):02d}KT")
    if vis_m is not None:
        bits.append("9999" if vis_m >= 9999 else f"{int(vis_m):04d}")
    if temp is not None:
        t = int(round(temp))
        d = int(round(dew)) if dew is not None else t
        def fmt(v: int) -> str:
            return f"M{abs(v):02d}" if v < 0 else f"{v:02d}"
        bits.append(f"{fmt(t)}/{fmt(d)}")
    if qnh is not None:
        bits.append(f"Q{int(round(qnh)):04d}")
    return " ".join(bits) if len(bits) > 1 else ""


_RWY_TOKEN = re.compile(
    r"\b(\d{1,2})(?:\s*(LEFT|RIGHT|CENTER|CENTRE|[LRC]))?\b",
    re.I,
)
_ATIS_STOP = r"TRANSITION|ACKNOWLEDGE|EXPECT|ADVISE|INFORMATION|AND\s+ADVISE"


def _rwy_ident(num: str, suf: str | None) -> str:
    ident = f"{int(num):02d}"
    s = (suf or "").upper()
    if s in ("LEFT", "L"):
        ident += "L"
    elif s in ("RIGHT", "R"):
        ident += "R"
    elif s in ("CENTER", "CENTRE", "C"):
        ident += "C"
    return ident


def _rwy_tokens(blob: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for m in _RWY_TOKEN.finditer(blob or ""):
        ident = _rwy_ident(m.group(1), m.group(2))
        if ident in seen:
            continue
        seen.add(ident)
        out.append(ident)
    return out


def parse_atis_runways(atis: str) -> tuple[list[str], list[str]]:
    """DEP/ARR runways as published in BeyondATC D-ATIS — no inference."""
    text = (atis or "").upper()
    if not text:
        return [], []
    dep_m = re.search(
        rf"\bDEP(?:ARTURE)?\s+RWYS?\s+(.+?)(?=\bARR(?:IVAL)?\s+RWY|\b{_ATIS_STOP}|$)",
        text,
    )
    arr_m = re.search(
        rf"\bARR(?:IVAL)?\s+RWYS?\s+(.+?)(?=\bDEP(?:ARTURE)?\s+RWY|\b{_ATIS_STOP}|$)",
        text,
    )
    dep = _rwy_tokens(dep_m.group(1) if dep_m else "")
    arr = _rwy_tokens(arr_m.group(1) if arr_m else "")
    if dep or arr:
        return dep, arr
    use_m = re.search(r"\bRWYS?\s+(.+?)\s+IN USE\b", text)
    used = _rwy_tokens(use_m.group(1) if use_m else "")
    return used, used


def _norm_rwy(raw: str) -> str:
    text = (raw or "").strip().upper()
    if not text:
        return ""
    nav = _nav_norm_rwy(text)
    if nav:
        return nav
    tokens = _rwy_tokens(text.replace("RWY", " ").replace("RUNWAY", " ").replace("RW", " "))
    return tokens[0] if tokens else ""


def _wx_for_icao(code: str, text: str) -> str:
    raw = (text or "").strip()
    if not raw or len(code) != 4:
        return raw
    u = raw.upper()
    m = re.match(r"^(?:METAR(?:\s+COR)?\s+)?([A-Z]{4})\b", u)
    if m and m.group(1) != code:
        return ""
    if re.match(r"^[A-Z]{4}\b", u) and not u.startswith(code):
        return ""
    return raw


def _airport_rwys(icao: str) -> list[str]:
    return [row[0] for row in list_runways(icao)]


def _keep_local_rwys(idents: list[str], allowed: list[str]) -> list[str]:
    if not idents:
        return []
    if not allowed:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for ident in idents:
        n = _norm_rwy(ident)
        hit = next((a for a in allowed if _same_rwy(n, a)), "")
        if not hit or hit in seen:
            continue
        seen.add(hit)
        out.append(hit)
    return out


def runways_from_traffic(aircraft: list[dict], icao: str) -> tuple[list[str], list[str]]:
    """DEP/ARR in use from loaded AI flight plans at this airport."""
    code = (icao or "").upper()
    dep_votes: dict[str, int] = {}
    arr_votes: dict[str, int] = {}
    for ac in aircraft or []:
        if ac.get("isOwnship") or ac.get("isPlayer"):
            continue
        origin = str(ac.get("origin") or "").upper()
        dest = str(ac.get("dest") or "").upper()
        dep = _norm_rwy(str(ac.get("depRunway") or ""))
        arr = _norm_rwy(str(ac.get("arrRunway") or ""))
        if origin == code and dep:
            dep_votes[dep] = dep_votes.get(dep, 0) + 1
        if dest == code and arr:
            arr_votes[arr] = arr_votes.get(arr, 0) + 1

    def ranked(votes: dict[str, int]) -> list[str]:
        return [ident for ident, _n in sorted(votes.items(), key=lambda kv: (-kv[1], kv[0]))]

    return ranked(dep_votes), ranked(arr_votes)


def atis_from_traffic(aircraft: list[dict], icao: str) -> tuple[str, str]:
    code = (icao or "").upper()
    for ac in aircraft or []:
        name = (ac.get("freqName") or "")
        if "ATIS" not in name.upper():
            continue
        here = (ac.get("origin") or "").upper() == code or (ac.get("dest") or "").upper() == code
        if here and ac.get("freq"):
            return str(ac.get("freq") or ""), name
    return "", ""


def build(
    *,
    icao: str,
    iata: str = "",
    name: str = "",
    city: str = "",
    airport_name: str = "",
    sim_metar: str = "",
    batc_metar: str = "",
    batc_atis: str = "",
    live_metar: str = "",
    wx: dict | None = None,
    atc_runway: str = "",
    aircraft: list[dict] | None = None,
    wx_pending: bool = False,
) -> dict:
    wx = wx or {}
    code = (icao or "").upper()
    metar = _wx_for_icao(code, batc_metar)
    source = "batc" if metar else ""
    if not metar:
        metar = _wx_for_icao(code, sim_metar)
        source = "msfs" if metar else ""
    if not metar:
        metar = _synthetic_metar(code, wx)
        source = "sim" if metar else ""
    if not metar and live_metar:
        metar = _wx_for_icao(code, live_metar)
        source = "live" if metar else ""
    parsed = _parse_metar(metar)
    wind_from = wx.get("windFrom")
    wind_kt = wx.get("windKt")
    if wind_from is None:
        wind_from = parsed.get("windFrom")
    if wind_kt is None:
        wind_kt = parsed.get("windKt")
    temp_c = wx.get("tempC") if wx.get("tempC") is not None else parsed.get("tempC")
    dew_c = wx.get("dewC") if wx.get("dewC") is not None else parsed.get("dewC")
    qnh = wx.get("qnh") if wx.get("qnh") is not None else parsed.get("qnh")
    vis_m = wx.get("visM") if wx.get("visM") is not None else parsed.get("visM")
    local = _airport_rwys(code)
    atis_text = _wx_for_icao(code, batc_atis)
    dep_use, arr_use = parse_atis_runways(atis_text)
    dep_use = _keep_local_rwys(dep_use, local)
    arr_use = _keep_local_rwys(arr_use, local)
    if not dep_use or not arr_use:
        traffic_dep, traffic_arr = runways_from_traffic(aircraft or [], code)
        traffic_dep = _keep_local_rwys(traffic_dep, local)
        traffic_arr = _keep_local_rwys(traffic_arr, local)
        if not dep_use:
            dep_use = traffic_dep
        if not arr_use:
            arr_use = traffic_arr
    atis, atis_name = atis_from_traffic(aircraft or [], code)
    rwy_atc = _keep_local_rwys([_norm_rwy(atc_runway)], local)
    rwy_atc = rwy_atc[0] if rwy_atc else ""
    return {
        "icao": code,
        "iata": iata or "",
        "name": name or city or airport_name or "",
        "city": city or "",
        "airportName": airport_name or name or "",
        "metar": metar,
        "metarSource": source,
        "wxPending": bool(wx_pending) and not (atis_text or metar),
        "atisText": atis_text,
        "windFrom": wind_from,
        "windKt": wind_kt,
        "tempC": temp_c,
        "dewC": dew_c,
        "qnh": qnh,
        "visM": vis_m,
        "atcRunway": rwy_atc,
        "depInUse": dep_use,
        "arrInUse": arr_use,
        "favoured": [],
        "runways": [],
        "atis": atis,
        "atisName": atis_name,
    }
