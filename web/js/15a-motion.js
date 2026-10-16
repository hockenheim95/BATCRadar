/** Standard: latest BATC sample at rAF. Experimental: delayed lat/lon interpolation. */

const RENDER_STANDARD = "standard";
const RENDER_EXPERIMENTAL = "experimental";

function renderPref() {
  const raw = localStorage.getItem("efb-render");
  return raw === RENDER_EXPERIMENTAL ? RENDER_EXPERIMENTAL : RENDER_STANDARD;
}

let renderMode = renderPref();

function isExperimentalRender() {
  return renderMode === RENDER_EXPERIMENTAL;
}

function setRenderMode(mode) {
  const next = mode === RENDER_EXPERIMENTAL ? RENDER_EXPERIMENTAL : RENDER_STANDARD;
  if (next === renderMode) return;
  renderMode = next;
  localStorage.setItem("efb-render", next);
  motionById.clear();
  ensurePaintMotion();
}

const MOTION_MAX_MS = 2000;
const MOTION_MIN_MS = 120;
const MOTION_SNAP_KM = 6;
const MOTION_STILL_KM = 0.012;
const MOTION_STILL_GS = 1.2;
const MOTION_EXTRAP_S = 0.55;
const MOTION_FRAME_MS = 33;

const motionById = new Map();

function lerpNum(a, b, t) {
  return a + (b - a) * t;
}

function lerpLon(a, b, t) {
  let d = b - a;
  if (d > 180) d -= 360;
  if (d < -180) d += 360;
  return a + d * t;
}

function lerpHdg(a, b, t) {
  let d = (Number(b) || 0) - (Number(a) || 0);
  if (d > 180) d -= 360;
  if (d < -180) d += 360;
  return (((Number(a) || 0) + d * t) % 360 + 360) % 360;
}

function poseOfAc(ac) {
  return {
    lat: Number(ac.lat),
    lon: Number(ac.lon),
    alt: Number(ac.alt),
    hdg: headingOf(ac),
    gs: Number(ac.gs) || 0,
  };
}

function validPose(p) {
  return Boolean(p && Number.isFinite(p.lat) && Number.isFinite(p.lon));
}

function writePose(ac, p) {
  if (!ac || !validPose(p)) return;
  ac.lat = p.lat;
  ac.lon = p.lon;
  if (Number.isFinite(p.hdg)) ac.hdg = p.hdg;
}

function sampleChanged(a, b) {
  if (!a || !validPose(b)) return true;
  const d = kmBetween([a.lat, a.lon], [b.lat, b.lon]);
  return d != null && d > MOTION_STILL_KM;
}

function motionState(id) {
  let m = motionById.get(id);
  if (!m) {
    m = { samples: [], delay: 0, playT: 0, pose: null };
    motionById.set(id, m);
  }
  return m;
}

function stampPose(pose, now) {
  return { lat: pose.lat, lon: pose.lon, alt: pose.alt, hdg: pose.hdg, gs: pose.gs, t: now };
}

function snapMotion(id, pose, now) {
  const m = motionState(id);
  const p = stampPose(pose, now);
  m.samples = [p];
  m.delay = 0;
  m.playT = now;
  m.pose = { lat: p.lat, lon: p.lon, alt: p.alt, hdg: p.hdg, gs: p.gs };
}

function noteMotion(ac) {
  if (!ac || !ac.id) return;
  const now = performance.now();
  const pose = poseOfAc(ac);
  if (!validPose(pose)) return;
  const m = motionById.get(ac.id);
  if (!m || !m.samples.length) {
    snapMotion(ac.id, pose, now);
    return;
  }
  const last = m.samples[m.samples.length - 1];
  if (!sampleChanged(last, pose)) {
    last.alt = pose.alt;
    last.hdg = pose.hdg;
    last.gs = pose.gs;
    return;
  }
  const here = m.pose || last;
  const jump = kmBetween([here.lat, here.lon], [pose.lat, pose.lon]);
  if (jump != null && jump > MOTION_SNAP_KM) {
    snapMotion(ac.id, pose, now);
    return;
  }
  const stillKm = MOTION_STILL_KM;
  if ((pose.gs || 0) < MOTION_STILL_GS && (jump == null || jump < stillKm * 2.5)) {
    last.lat = pose.lat;
    last.lon = pose.lon;
    last.alt = pose.alt;
    last.hdg = pose.hdg;
    last.gs = pose.gs;
    return;
  }
  const dt = now - last.t;
  if (dt > 20) {
    const next = Math.min(MOTION_MAX_MS, Math.max(MOTION_MIN_MS, dt * 1.2));
    m.delay = m.delay ? m.delay * 0.75 + next * 0.25 : next;
  }
  m.samples.push(stampPose(pose, now));
  if (m.samples.length > 4) m.samples.shift();
}

function lerpPose(a, b, u) {
  return {
    lat: lerpNum(a.lat, b.lat, u),
    lon: lerpLon(a.lon, b.lon, u),
    alt: b.alt,
    hdg: lerpHdg(a.hdg, b.hdg, u),
    gs: lerpNum(a.gs || 0, b.gs || 0, u),
  };
}

function extrapolatePose(p, dtSec) {
  if (!p || dtSec <= 0 || (p.gs || 0) < 8) return { lat: p.lat, lon: p.lon, alt: p.alt, hdg: p.hdg, gs: p.gs };
  if (typeof destFrom !== "function") return { lat: p.lat, lon: p.lon, alt: p.alt, hdg: p.hdg, gs: p.gs };
  const nm = (p.gs * dtSec) / 3600;
  if (nm < 0.0005) return { lat: p.lat, lon: p.lon, alt: p.alt, hdg: p.hdg, gs: p.gs };
  const ll = destFrom(p.lat, p.lon, p.hdg || 0, nm);
  return { lat: ll[0], lon: ll[1], alt: p.alt, hdg: p.hdg, gs: p.gs };
}

function poseFromSample(s) {
  return { lat: s.lat, lon: s.lon, alt: s.alt, hdg: s.hdg, gs: s.gs };
}

function motionPoseAt(id, now) {
  const m = motionById.get(id);
  if (!m || !m.samples.length) return null;
  const samples = m.samples;
  if (samples.length === 1) {
    m.pose = poseFromSample(samples[0]);
    return m.pose;
  }
  if (!isExperimentalRender()) {
    m.pose = poseFromSample(samples[samples.length - 1]);
    return m.pose;
  }
  if (!m.delay) m.delay = MOTION_MIN_MS;
  const play = Math.max(m.playT, now - m.delay);
  m.playT = play;
  let a = samples[0];
  let b = samples[samples.length - 1];
  for (let i = 0; i < samples.length - 1; i += 1) {
    if (play <= samples[i + 1].t) {
      a = samples[i];
      b = samples[i + 1];
      break;
    }
    a = samples[i + 1];
    b = samples[i + 1];
  }
  if (play <= a.t) {
    m.pose = poseFromSample(a);
    return m.pose;
  }
  if (play >= b.t || a === b || !(b.t > a.t)) {
    const extra = Math.min(MOTION_EXTRAP_S, Math.max(0, play - b.t) / 1000);
    m.pose = extra > 0.02 ? extrapolatePose(b, extra) : poseFromSample(b);
    return m.pose;
  }
  m.pose = lerpPose(a, b, (play - a.t) / (b.t - a.t));
  return m.pose;
}

function forgetMotion(id) {
  if (id) motionById.delete(id);
}

function resetMotion(id, ac) {
  if (!id) return;
  if (ac) snapMotion(id, poseOfAc(ac), performance.now());
  else motionById.delete(id);
}

function applyMotionNow(ac) {
  if (!ac || !ac.id) return null;
  const pose = motionPoseAt(ac.id, performance.now());
  if (pose) writePose(ac, pose);
  return pose;
}

function displayedPoseOf(ac) {
  if (!ac || !ac.id) return null;
  const m = motionById.get(ac.id);
  if (m && validPose(m.pose)) return m.pose;
  return motionPoseAt(ac.id, performance.now());
}

function ptLatLon(pt) {
  if (!pt) return null;
  const lat = Number(pt.lat ?? pt[0]);
  const lon = Number(pt.lng ?? pt.lon ?? pt[1]);
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
  return [lat, lon];
}

function isAheadOfPose(pt, pose) {
  const ll = ptLatLon(pt);
  if (!ll || !validPose(pose)) return false;
  const d = kmBetween(ll, [pose.lat, pose.lon]);
  if (d == null || d < 0.03) return false;
  if (!Number.isFinite(pose.hdg) || typeof bearingTo !== "function" || typeof headingDiff !== "function") {
    return false;
  }
  return headingDiff(bearingTo([pose.lat, pose.lon], ll), pose.hdg) < 75;
}

function clipPtsBehindPose(pts, ac) {
  if (!pts || pts.length < 2 || !ac || !ac.id) return pts;
  const pose = displayedPoseOf(ac);
  if (!validPose(pose)) return pts;
  const poseLL = [pose.lat, pose.lon];
  const out = pts.slice();
  const latest = motionById.get(ac.id)?.samples?.slice(-1)[0];
  while (out.length > 1) {
    const last = out[out.length - 1];
    const prev = out[out.length - 2];
    if (typeof projectOnSeg === "function") {
      const hit = projectOnSeg(prev, last, poseLL);
      if (hit && Number.isFinite(hit.d) && hit.d < 2) {
        if (hit.t < 0.98) {
          out.pop();
          break;
        }
        if (isAheadOfPose(last, pose)) {
          out.pop();
          continue;
        }
        break;
      }
    }
    const dLast = kmBetween(last, poseLL);
    const dPrev = kmBetween(prev, poseLL);
    if (dLast != null && dLast < 0.04) break;
    if (latest && dLast != null && dPrev != null && dPrev <= dLast) {
      const dLastLatest = kmBetween(last, [latest.lat, latest.lon]);
      const dPoseLatest = kmBetween(poseLL, [latest.lat, latest.lon]);
      if (dLastLatest != null && dPoseLatest != null && dLastLatest + 0.02 < dPoseLatest) {
        out.pop();
        continue;
      }
    }
    if (isAheadOfPose(last, pose)) {
      out.pop();
      continue;
    }
    break;
  }
  return out;
}

function layerPointUnrounded(lat, lon) {
  const projected = map.project([lat, lon]);
  const origin = map.getPixelOrigin();
  return L.point(projected.x - origin.x, projected.y - origin.y);
}

function strokePathUnrounded(line) {
  if (!line || !line._path) return;
  const latlngs = line.getLatLngs();
  if (!latlngs || latlngs.length < 2) return;
  let d = "";
  for (let i = 0; i < latlngs.length; i += 1) {
    const ll = latlngs[i];
    const p = layerPointUnrounded(ll.lat, ll.lng);
    d += (i ? "L" : "M") + p.x + " " + p.y;
  }
  line._path.setAttribute("d", d);
}

function setMarkerLatLngSmooth(marker, lat, lon) {
  if (!marker || !Number.isFinite(lat) || !Number.isFinite(lon)) return;
  if (marker._latlng) {
    marker._latlng.lat = lat;
    marker._latlng.lng = lon;
  } else {
    marker._latlng = L.latLng(lat, lon);
  }
  if (!marker._map || !marker._icon) return;
  const origin = map.getPixelOrigin();
  const pt = layerPointUnrounded(lat, lon);
  const prev = marker._efbPt;
  if (
    prev &&
    marker._efbOx === origin.x &&
    marker._efbOy === origin.y &&
    Math.abs(prev.x - pt.x) < 0.25 &&
    Math.abs(prev.y - pt.y) < 0.25
  ) {
    return;
  }
  marker._efbPt = pt;
  marker._efbOx = origin.x;
  marker._efbOy = origin.y;
  if (typeof marker._setPos === "function") marker._setPos(pt);
  else marker.setLatLng(marker._latlng);
}

function placeMarkerPose(marker, ac, pose) {
  if (!marker || !ac || !validPose(pose)) return;
  writePose(ac, pose);
  setMarkerLatLngSmooth(marker, pose.lat, pose.lon);
  applyHeading(marker, ac);
}

function liveLineAc() {
  if (trailAcId && markers.has(trailAcId)) return markers.get(trailAcId).acData;
  return null;
}

function writeLineLatLngs(line, pairs) {
  if (!line || !pairs || pairs.length < 2) return;
  const pts = line.getLatLngs();
  if (pts && pts.length === pairs.length) {
    let changed = false;
    for (let i = 0; i < pairs.length; i += 1) {
      if (pts[i].lat !== pairs[i][0] || pts[i].lng !== pairs[i][1]) {
        pts[i].lat = pairs[i][0];
        pts[i].lng = pairs[i][1];
        changed = true;
      }
    }
    if (changed) strokePathUnrounded(line);
    return;
  }
  line.setLatLngs(pairs);
  strokePathUnrounded(line);
}

function trailAnchorLatLng(ac, pose) {
  const groups = [];
  if (typeof trailLiveGroup !== "undefined") groups.push(trailLiveGroup);
  if (typeof trail !== "undefined") groups.push(trail);
  for (const group of groups) {
    const layers = group.getLayers();
    for (let i = layers.length - 1; i >= 0; i -= 1) {
      const pts = layers[i].getLatLngs();
      if (!pts || !pts.length) continue;
      for (let j = pts.length - 1; j >= 0; j -= 1) {
        const p = pts[j];
        if (isExperimentalRender() && validPose(pose) && isAheadOfPose(p, pose)) continue;
        return p;
      }
    }
  }
  const m = ac && ac.id ? motionById.get(ac.id) : null;
  if (m && m.samples && m.samples.length) {
    let s = m.samples[0];
    for (let i = 0; i < m.samples.length; i += 1) {
      if (m.samples[i].t <= (m.playT || 0)) s = m.samples[i];
    }
    return { lat: s.lat, lng: s.lon };
  }
  return null;
}

function nudgeLiveTrail(ac, pose) {
  if (!trailLive || !validPose(pose)) return;
  const anchor = trailAnchorLatLng(ac, pose);
  if (!anchor) {
    trailLive.setLatLngs([]);
    return;
  }
  const alt = Number.isFinite(pose.alt) ? pose.alt : Number(ac && ac.alt);
  if (typeof altColor === "function") {
    const col = altColor(alt, fieldElevOf(ac));
    if (trailLive.options.color !== col) trailLive.setStyle({ color: col });
  }
  writeLineLatLngs(trailLive, [
    [anchor.lat, anchor.lng],
    [pose.lat, pose.lon],
  ]);
}

function nudgeLiveRoute(ac, pose) {
  if (!ac || !validPose(pose) || hideDestRouteAtDest(ac)) return;
  const dest = destLL(ac);
  if (!dest) return;
  const line = ac.isOwnship ? ownRoute : selRoute;
  const pairs =
    typeof directDestRoutePts === "function"
      ? directDestRoutePts(dest, pose.lat, pose.lon)
      : [dest, [pose.lat, pose.lon]];
  writeLineLatLngs(line, pairs);
}

let lastMotionPaint = 0;
let paintMotionRaf = 0;
let paintMotionTimer = 0;

function viewCullBounds(pad) {
  const size = map.getSize();
  if (!size || !size.x) return null;
  const sw = map.containerPointToLatLng([-pad, size.y + pad]);
  const ne = map.containerPointToLatLng([size.x + pad, -pad]);
  return {
    minLat: Math.min(sw.lat, ne.lat),
    maxLat: Math.max(sw.lat, ne.lat),
    minLon: Math.min(sw.lng, ne.lng),
    maxLon: Math.max(sw.lng, ne.lng),
  };
}

function inCullBounds(b, lat, lon) {
  if (!b) return true;
  return lat >= b.minLat && lat <= b.maxLat && lon >= b.minLon && lon <= b.maxLon;
}

function paintOwnshipNow(now) {
  const pose = motionPoseAt("ownship", now);
  if (!validPose(pose)) return pose;
  if (lastOwn) writePose(lastOwn, pose);
  if (typeof followOwnshipSmooth === "function") followOwnshipSmooth(now);
  const mk = markers.get("ownship");
  if (mk && mk.acData) placeMarkerPose(mk, mk.acData, pose);
  const lined = liveLineAc();
  if (lined && (lined.isOwnship || lined.id === "ownship")) {
    nudgeLiveTrail(lined, pose);
    nudgeLiveRoute(lined, pose);
  }
  return pose;
}

function paintLinedNow(now) {
  const lined = liveLineAc();
  if (!lined || !lined.id || lined.isOwnship || lined.id === "ownship") return;
  const pose = motionPoseAt(lined.id, now);
  if (!validPose(pose)) return;
  const mk = markers.get(lined.id);
  if (mk) placeMarkerPose(mk, lined, pose);
  else writePose(lined, pose);
  nudgeLiveTrail(lined, pose);
  nudgeLiveRoute(lined, pose);
}

function paintTrafficNow(now) {
  lastMotionPaint = now;
  const cull = viewCullBounds(96);
  const linedId = liveLineAc()?.id;
  for (const marker of markers.values()) {
    const ac = marker.acData;
    if (!ac || !ac.id || ac.isOwnship || ac.id === "ownship") continue;
    const pose = motionPoseAt(ac.id, now);
    if (!validPose(pose)) continue;
    writePose(ac, pose);
    if (ac.id === linedId) continue;
    if (!inCullBounds(cull, pose.lat, pose.lon)) {
      if (marker._latlng) {
        marker._latlng.lat = pose.lat;
        marker._latlng.lng = pose.lon;
      }
      continue;
    }
    placeMarkerPose(marker, ac, pose);
  }
}

function paintPeriodMs() {
  if (!isExperimentalRender()) return 0;
  return TOUCH_UI ? MOTION_FRAME_MS : 16;
}

function armPaintMotion(delayMs) {
  if (document.hidden) {
    stopPaintMotion();
    return;
  }
  if (paintMotionRaf) {
    cancelAnimationFrame(paintMotionRaf);
    paintMotionRaf = 0;
  }
  if (paintMotionTimer) {
    clearTimeout(paintMotionTimer);
    paintMotionTimer = 0;
  }
  paintMotion._active = true;
  if (delayMs > 4) {
    paintMotionTimer = setTimeout(() => {
      paintMotionTimer = 0;
      paintMotionRaf = requestAnimationFrame(paintMotion);
    }, delayMs);
  } else {
    paintMotionRaf = requestAnimationFrame(paintMotion);
  }
}

function paintMotion(now) {
  paintMotionRaf = 0;
  if (document.hidden) {
    stopPaintMotion();
    return;
  }
  const experimental = isExperimentalRender();
  const ownEvery = TOUCH_UI ? MOTION_FRAME_MS : 16;
  if (!experimental || !paintMotion._ownAt || now - paintMotion._ownAt >= ownEvery) {
    paintMotion._ownAt = now;
    paintOwnshipNow(now);
    paintLinedNow(now);
  }
  if (!experimental || now - lastMotionPaint >= MOTION_FRAME_MS) {
    paintTrafficNow(now);
  }
  if (!experimental) {
    paintMotion._active = false;
    return;
  }
  const wait = Math.max(0, paintPeriodMs() - (performance.now() - now));
  armPaintMotion(wait);
}

function stopPaintMotion() {
  paintMotion._active = false;
  if (paintMotionRaf) {
    cancelAnimationFrame(paintMotionRaf);
    paintMotionRaf = 0;
  }
  if (paintMotionTimer) {
    clearTimeout(paintMotionTimer);
    paintMotionTimer = 0;
  }
}

function ensurePaintMotion() {
  if (paintMotion._active || document.hidden) return;
  armPaintMotion(0);
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden) stopPaintMotion();
  else ensurePaintMotion();
});

ensurePaintMotion();
