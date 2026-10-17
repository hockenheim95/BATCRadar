function kmBetween(a, b) {
  if (!a || !b) return null;
  const r = 6371;
  const p1 = (a[0] * Math.PI) / 180;
  const p2 = (b[0] * Math.PI) / 180;
  const d1 = ((b[0] - a[0]) * Math.PI) / 180;
  const d2 = ((b[1] - a[1]) * Math.PI) / 180;
  const h = Math.sin(d1 / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(d2 / 2) ** 2;
  return 2 * r * Math.asin(Math.min(1, Math.sqrt(h)));
}

function fmtNearDist(km) {
  if (km == null || Number.isNaN(Number(km))) return "";
  const n = Number(km);
  const kmTxt = `${Math.round(n).toLocaleString("de-DE")} km`;
  const miTxt = `${Math.round(n / 1.609344).toLocaleString("de-DE")} mi`;
  const pref = unitPref();
  if (pref === "metric") return kmTxt;
  if (pref === "us") return miTxt;
  return useMetricUnits() ? kmTxt : miTxt;
}

function fmtDur(ms) {
  const s = Math.max(0, Math.round(Math.abs(ms) / 1000));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h) return `${h}:${String(m).padStart(2, "0")}`;
  return `00:${String(m).padStart(2, "0")}`;
}

/** Arrival delay minutes from server FIDS board latch (fids.py). */
function arrDelayMinutes(ac) {
  if (typeof fidsArrLate === "undefined") return null;
  const latched = fidsArrLate.get(ac.id);
  return latched != null ? latched : null;
}

function planBit(text) {
  return String(text || "")
    .replace(/^RWY\s+/i, "")
    .replace("—", "")
    .trim()
    .toUpperCase();
}

function flightPlanLine(ac) {
  if (!ac) return "";
  const orig = planBit(ac.origin);
  const dest = planBit(ac.dest);
  const left = [orig, planBit(ac.depRunway), planBit(ac.sid)].filter(Boolean).join(" ");
  const right = [planBit(ac.star), planBit(ac.arrRunway), dest].filter(Boolean).join(" ");
  if (left && right) return `${left} → ${right}`;
  return left || right;
}

function setFlightPlanLine(ac) {
  setText("p-route-plan", flightPlanLine(ac));
}

function setProgress(ac) {
  const box = document.getElementById("p-progress");
  const mid = document.getElementById("p-route-mid");
  if (!ac) return;
  setFlightPlanLine(ac);
  paintRoutePhaseSign(ac);
  const aptTitle = document.getElementById("apt-box-title");
  if (aptTitle?.textContent === "Flight Information") {
    const tz = tzForEnroute(ac);
    if (tz && tz !== aptClockTz) startAptClock(tz);
  }
  const origin = originLL(ac);
  const dest = destLL(ac);
  const barOff = !origin || !dest;
  box.hidden = false;
  const meta = box.querySelector(".progress-meta");
  const track = box.querySelector(".progress-track");
  if (meta) meta.hidden = barOff;
  if (track) track.hidden = barOff;
  setAirportTimes(ac);
  if (barOff) return;
  const here = [ac.lat, ac.lon];
  const flown = kmBetween(origin, here) || 0;
  const remain = kmBetween(here, dest) || 0;
  const total = flown + remain;
  const pct = total > 1 ? Math.max(0, Math.min(100, (flown / total) * 100)) : isGrounded(ac) && ac.atDestination ? 100 : 0;
  document.getElementById("p-prog-fill").style.width = `${pct}%`;
  const dot = document.getElementById("p-prog-dot");
  if (dot) dot.style.left = `${pct}%`;
  const dep = parseBatcZForAc(ac, ac.timeFrom);
  const arr = parseBatcZForAc(ac, ac.timeTo);
  const now = Date.now();
  let left = fmtNearDist(flown);
  let right = fmtNearDist(remain);
  if (dep && now >= dep.getTime()) left += left ? ` · ${fmtDur(now - dep.getTime())} ago` : "";
  else if (dep) left += left ? ` · in ${fmtDur(dep.getTime() - now)}` : "";
  if (arr && arr.getTime() > now) right += right ? ` · in ${fmtDur(arr.getTime() - now)}` : "";
  else if (arr) {
    right += right ? " · delayed" : "delayed";
    const late = arrDelayMinutes(ac);
    if (late != null) right += ` +${late}`;
  }
  setText("p-prog-left", left);
  setText("p-prog-right", right);
}

function destLL(ac) {
  if (!ac) return null;
  if (ac.destLat != null && ac.destLon != null) return [ac.destLat, ac.destLon];
  return null;
}

function originLL(ac) {
  if (!ac) return null;
  if (ac.originLat != null && ac.originLon != null) return [ac.originLat, ac.originLon];
  return null;
}

function bearingTo(a, b) {
  const lat1 = (a[0] * Math.PI) / 180;
  const lat2 = (b[0] * Math.PI) / 180;
  const dlon = ((b[1] - a[1]) * Math.PI) / 180;
  const y = Math.sin(dlon) * Math.cos(lat2);
  const x = Math.cos(lat1) * Math.sin(lat2) - Math.sin(lat1) * Math.cos(lat2) * Math.cos(dlon);
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
}

function headingArc(from, fromHdg, toHdg, radiusKm) {
  let dH = ((toHdg - fromHdg + 540) % 360) - 180;
  const left = dH < 0;
  const mag = Math.min(Math.abs(dH), 110);
  if (mag < 8 || !Number.isFinite(radiusKm) || radiusKm < 0.5) return [from.slice()];
  const rnm = radiusKm / 1.852;
  const ctr = destFrom(from[0], from[1], fromHdg + (left ? -90 : 90), rnm);
  const steps = Math.max(6, Math.min(28, Math.ceil(mag / 5)));
  const out = [from.slice()];
  for (let i = 1; i <= steps; i += 1) {
    const hdg = fromHdg + (mag * (i / steps) * (left ? -1 : 1));
    const radial = hdg + (left ? 90 : -90);
    out.push(destFrom(ctr[0], ctr[1], radial, rnm));
  }
  return out;
}

function noSidDepart(ac, join) {
  if (ac.depRwyLat == null || ac.depRwyLon == null) return [];
  const start = [ac.depRwyLat, ac.depRwyLon];
  const hdg = Number(ac.depRwyHdg);
  if (!Number.isFinite(hdg)) return [start];
  const end =
    ac.depRwyEndLat != null
      ? [ac.depRwyEndLat, ac.depRwyEndLon]
      : destFrom(start[0], start[1], hdg, 2);
  const climb = destFrom(end[0], end[1], hdg, 1);
  const pts = [start, end, climb];
  if (!join) return pts;
  const want = bearingTo(climb, join);
  const kt = Math.max(200, Number(ac.gs) || 250);
  const arc = headingArc(climb, hdg, want, Math.max(3.2, turnRadiusKm(kt, 25)));
  for (let i = 1; i < arc.length; i += 1) pts.push(arc[i]);
  return pts;
}

function destFrom(lat, lon, heading, nm) {
  const r = 3440.065;
  const brng = (heading * Math.PI) / 180;
  const lat1 = (lat * Math.PI) / 180;
  const lon1 = (lon * Math.PI) / 180;
  const lat2 = Math.asin(
    Math.sin(lat1) * Math.cos(nm / r) + Math.cos(lat1) * Math.sin(nm / r) * Math.cos(brng)
  );
  const lon2 =
    lon1 +
    Math.atan2(
      Math.sin(brng) * Math.sin(nm / r) * Math.cos(lat1),
      Math.cos(nm / r) - Math.sin(lat1) * Math.sin(lat2)
    );
  return [(lat2 * 180) / Math.PI, (lon2 * 180) / Math.PI];
}

function isGrounded(ac) {
  if (!ac) return true;
  if (ac.onGround || ac.atDestination) return true;
  const st = (ac.state || "").toLowerCase();
  return st === "parked" || st === "taxi" || st === "takeoff";
}

const PARKED_DEP_WINDOW_MS = 30 * 60 * 1000;
const PARKED_DEP_BYPASS_MAX = 20;

function mapVisible(ac) {
  if (!ac) return false;
  if (ac.isOwnship) return true;
  if (
    typeof fidsDepListed === "function" &&
    typeof fidsDepBoardCount === "function" &&
    fidsDepBoardCount(ac) < PARKED_DEP_BYPASS_MAX &&
    fidsDepListed(ac)
  ) {
    return true;
  }
  const st = String(ac.state || "").toLowerCase();
  if (st !== "parked") return true;
  const now = Date.now();
  const std = parseBatcZForAc(ac, ac.timeFrom);
  if (std) {
    const untilDep = std.getTime() - now;
    if (untilDep >= 0 && untilDep <= PARKED_DEP_WINDOW_MS) return true;
  }
  const sta = parseBatcZForAc(ac, ac.timeTo);
  if (sta && (ac.atDestination || atDestField(ac))) {
    const sinceArr = now - sta.getTime();
    if (sinceArr >= 0 && sinceArr <= PARKED_DEP_WINDOW_MS) return true;
  }
  return false;
}

function clearLine(line) {
  const pts = line.getLatLngs();
  if (!pts || !pts.length) {
    if (line) {
      line._efbPathPrefix = "";
      line._efbOriginKey = "";
    }
    return;
  }
  line._efbPathPrefix = "";
  line._efbOriginKey = "";
  line.setLatLngs([]);
}

function atDestField(ac) {
  const dest = destLL(ac);
  if (!dest || !ac) return false;
  if (!ac.onGround && (ac.gs || 0) > 40) return false;
  return (kmBetween([ac.lat, ac.lon], dest) || 99) < 8;
}

function applyDestRouteLine(line, dest, lat, lon) {
  const pairs =
    typeof directDestRoutePts === "function" ? directDestRoutePts(dest, lat, lon) : [dest, [lat, lon]];
  if (typeof writeLineLatLngs === "function") writeLineLatLngs(line, pairs);
  else line.setLatLngs(pairs);
}

/** Hide direct-to-dest line once rolling has finished and ground ops begin at destination. */
function hideDestRouteAtDest(ac) {
  if (!ac || !atDestField(ac)) return false;
  const st = (ac.state || "").toLowerCase();
  return st === "taxi" || st === "parked" || st === "pushback";
}

function setRoute(line, ac, opts = {}) {
  const dest = destLL(ac);
  if (!ac || !dest || !Number.isFinite(ac.lat) || !Number.isFinite(ac.lon) || hideDestRouteAtDest(ac)) {
    clearLine(line);
    return;
  }
  let lat = ac.lat;
  let lon = ac.lon;
  if (typeof motionById !== "undefined" && ac.id) {
    const pose = motionById.get(ac.id)?.pose;
    if (pose && Number.isFinite(pose.lat) && Number.isFinite(pose.lon)) {
      lat = pose.lat;
      lon = pose.lon;
    }
  }
  // Always full GC to dest — one aircraft polyline; writeLineLatLngs no-ops if unchanged.
  applyDestRouteLine(line, dest, lat, lon);
}

function syncRoutes(ac, opts = {}) {
  if (!ac || ac.isOwnship) {
    clearLine(selRoute);
    setRoute(ownRoute, ac, opts);
    return;
  }
  clearLine(ownRoute);
  setRoute(selRoute, ac, opts);
}

const trackCache = new Map();
let histSeq = 0;
let paintedReconKey = null;
let paintedLiveSig = "";

