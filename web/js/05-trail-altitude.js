function lerpStops(x, xs, ys) {
  if (x <= xs[0]) return ys[0].slice ? ys[0].slice() : ys[0];
  const last = xs.length - 1;
  if (x >= xs[last]) return ys[last].slice ? ys[last].slice() : ys[last];
  for (let i = 1; i < xs.length; i += 1) {
    if (x <= xs[i]) {
      const t = (x - xs[i - 1]) / (xs[i] - xs[i - 1] || 1);
      const a = ys[i - 1];
      const b = ys[i];
      if (Array.isArray(a)) return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
      return a + (b - a) * t;
    }
  }
  return ys[last].slice ? ys[last].slice() : ys[last];
}

const CLIMB_NM = [0, 0.4, 1.2, 3, 6, 12, 18, 26, 40, 55, 75, 100, 130];
const CLIMB_AGL = [0, 500, 1500, 3000, 5000, 9000, 12000, 15000, 20000, 25000, 30000, 35000, 39000];
const ALT_FT = [0, 2500, 10000, 20000, 30000, 42000];
const ALT_RGB = [
  [255, 214, 32],
  [255, 188, 0],
  [34, 197, 94],
  [37, 99, 235],
  [168, 85, 247],
  [124, 58, 237],
];

function climbScale(ac) {
  const shape = shapeOf(ac);
  if (shape === "prop") return 1.85;
  if (shape === "quad") return 1.22;
  if (shape === "bizjet") return 0.72;
  return 1;
}

function fieldElevOf(ac, livePts) {
  const tagged = Number(ac && ac.trackFieldAlt);
  if (plausibleFieldAlt(ac, tagged)) return tagged;
  const local = ac && localFieldAlt.get(ac.id);
  if (plausibleFieldAlt(ac, local)) return local;
  if (ac && ac.onGround) {
    const a = Number(ac.alt);
    if (plausibleFieldAlt(ac, a)) return a;
  }
  const first = livePts && livePts[0];
  const fa = ptAlt(first);
  if (fa != null && fa < 2200) return fa;
  return 0;
}

function climbAglAt(nmFromStart, scale) {
  const roll = scale > 1.4 ? 1.35 : 1.15;
  const air = Math.max(0, nmFromStart - roll) / Math.max(0.45, scale);
  return lerpStops(air, CLIMB_NM, CLIMB_AGL);
}

function nmToReachAgl(agl, scale) {
  const target = Math.max(0, agl);
  if (target <= 40) return 0;
  let lo = 0;
  let hi = 170;
  for (let i = 0; i < 20; i += 1) {
    const mid = (lo + hi) / 2;
    if (climbAglAt(mid, scale) < target) lo = mid;
    else hi = mid;
  }
  return hi;
}

function descentNm(dAgl, scale) {
  const fpn = scale > 1.4 ? 240 : 320;
  return Math.max(6, Math.max(0, dAgl) / fpn);
}

function cruiseThatFits(totalNm, joinAgl, scale) {
  const want = 30000;
  const minC = Math.max(joinAgl, 2500);
  const needed = (cr) => nmToReachAgl(cr, scale) + descentNm(Math.max(0, cr - joinAgl), scale);
  if (needed(want) <= totalNm - 2) return want;
  let lo = minC;
  let hi = want;
  if (needed(minC) > totalNm) return minC;
  for (let i = 0; i < 18; i += 1) {
    const mid = (lo + hi) / 2;
    if (needed(mid) <= totalNm - 2) lo = mid;
    else hi = mid;
  }
  return Math.round(lo / 500) * 500;
}

function spawnIsDescent(ac, joinAgl, pts, reconCount) {
  if (joinAgl >= 22000) return false;
  if (ac && ac.atDestination) return true;
  const st = String((ac && ac.state) || "").toLowerCase();
  if (/approach|landing|arriv|descend|descent/.test(st)) return true;
  const dest = destLL(ac);
  if (dest && ac && kmBetween([ac.lat, ac.lon], dest) < 160 && joinAgl < 16000) return true;
  if (pts && reconCount >= 0 && pts.length > reconCount + 1) {
    const a = ptAlt(pts[reconCount]);
    const b = ptAlt(pts[pts.length - 1]);
    if (a != null && b != null && b < a - 1000) return true;
  }
  return false;
}

function fillClimbAlts(pts, reconCount, fieldFt, joinMsl, scale, ac) {
  if (reconCount < 1) return;
  const field = Number.isFinite(fieldFt) ? fieldFt : 0;
  const join = Number.isFinite(joinMsl) ? joinMsl : field;
  const joinAgl = Math.max(0, join - field);
  const dist = [0];
  for (let i = 1; i < reconCount; i += 1) {
    dist.push(dist[i - 1] + (kmBetween(pts[i - 1], pts[i]) || 0) / 1.852);
  }
  const totalNm = dist[dist.length - 1] || 0;
  if (spawnIsDescent(ac, joinAgl, pts, reconCount) && totalNm > 8) {
    const cruise = cruiseThatFits(totalNm, joinAgl, scale);
    let climbEnd = nmToReachAgl(cruise, scale);
    let descLen = descentNm(Math.max(0, cruise - joinAgl), scale);
    if (climbEnd + descLen > totalNm) {
      const share = totalNm / (climbEnd + descLen || 1);
      climbEnd *= share;
      descLen *= share;
    }
    const descStart = Math.max(climbEnd, totalNm - descLen);
    for (let i = 0; i < reconCount; i += 1) {
      const x = dist[i];
      let agl;
      if (x <= climbEnd) agl = Math.min(cruise, climbAglAt(x, scale));
      else if (x >= descStart && totalNm > descStart) {
        const t = (x - descStart) / (totalNm - descStart);
        agl = cruise + (joinAgl - cruise) * t;
      } else agl = cruise;
      pts[i][2] = field + Math.max(0, agl);
    }
    return;
  }
  for (let i = 0; i < reconCount; i += 1) {
    pts[i][2] = field + Math.min(joinAgl, climbAglAt(dist[i], scale));
  }
}

function liveBlendNm(pts, startIdx) {
  let total = 0;
  for (let i = startIdx + 1; i < pts.length; i += 1) {
    total += (kmBetween(pts[i - 1], pts[i]) || 0) / 1.852;
  }
  if (total < 1.5) return total * 0.85;
  return Math.min(20, Math.max(5, total * 0.38));
}

function smoothstep(t) {
  const x = Math.max(0, Math.min(1, t));
  return x * x * (3 - 2 * x);
}

function blendAltsFrom(pts, startIdx, fromMsl, blendNm) {
  if (!pts || startIdx >= pts.length || !Number.isFinite(fromMsl) || !(blendNm > 0)) return;
  let nm = 0;
  for (let i = startIdx; i < pts.length; i += 1) {
    if (i > startIdx) nm += (kmBetween(pts[i - 1], pts[i]) || 0) / 1.852;
    const measured = ptAlt(pts[i]);
    if (nm <= 0 || measured == null) {
      pts[i][2] = fromMsl;
      continue;
    }
    if (nm >= blendNm) break;
    const s = smoothstep(nm / blendNm);
    pts[i][2] = fromMsl + (measured - fromMsl) * s;
  }
}

/** Pull recon tail toward the first live altitude so color does not step at spawn. */
function blendReconTailToLive(pts, reconCount, targetMsl, blendNm) {
  if (reconCount < 2 || reconCount >= pts.length || !Number.isFinite(targetMsl) || !(blendNm > 0)) return;
  let nm = 0;
  for (let i = reconCount - 1; i >= 0; i -= 1) {
    const cur = ptAlt(pts[i]);
    if (!Number.isFinite(cur)) continue;
    if (i < reconCount - 1) nm += (kmBetween(pts[i], pts[i + 1]) || 0) / 1.852;
    if (nm > blendNm) break;
    const w = smoothstep(1 - nm / blendNm);
    pts[i][2] = cur + (targetMsl - cur) * w;
  }
}

function seamBlendNm(pts, reconCount) {
  const liveNm = liveBlendNm(pts, reconCount);
  let reconTail = 0;
  for (let i = reconCount - 1; i >= 1; i -= 1) {
    reconTail += (kmBetween(pts[i - 1], pts[i]) || 0) / 1.852;
  }
  let firstLeg = 0;
  if (reconCount > 0 && reconCount < pts.length) {
    firstLeg = (kmBetween(pts[reconCount - 1], pts[reconCount]) || 0) / 1.852;
  }
  return Math.min(42, Math.max(liveNm, reconTail * 0.55, firstLeg * 0.7, 8));
}

function assignTrailAlts(pts, ac, reconCount) {
  if (!pts.length) return;
  const spawnIdx = reconCount > 0 ? reconCount - 1 : -1;
  const spawnMeasured = spawnIdx >= 0 ? ptAlt(pts[spawnIdx]) : null;
  const field = fieldElevOf(ac, pts.slice(Math.min(pts.length, Math.max(0, reconCount))));
  const hereAlt = Number(ac && ac.alt);
  const join =
    spawnMeasured ??
    ptAlt(pts[reconCount]) ??
    ptAlt(pts[pts.length - 1]) ??
    (Number.isFinite(hereAlt) ? hereAlt : field);
  if (reconCount > 0) fillClimbAlts(pts, reconCount, field, join, climbScale(ac), ac);
  if (spawnIdx >= 0) pts[spawnIdx][2] = join;
  let last = spawnIdx >= 0 ? join : null;
  for (let i = reconCount; i < pts.length; i += 1) {
    const m = ptAlt(pts[i]);
    if (m != null) last = m;
    else pts[i][2] = last != null ? last : hereAlt;
  }
  if (reconCount > 0 && reconCount < pts.length) {
    const liveAlt = ptAlt(pts[reconCount]) ?? join;
    blendReconTailToLive(pts, reconCount, liveAlt, seamBlendNm(pts, reconCount));
  } else if (reconCount === 0) {
    const seamAlt = ptAlt(pts[0]);
    if (Number.isFinite(seamAlt)) blendAltsFrom(pts, 0, seamAlt, liveBlendNm(pts, 0));
  }
  if (Number.isFinite(hereAlt) && reconCount === 0) pts[pts.length - 1][2] = hereAlt;
}

function rgbCss(rgb) {
  return `rgb(${Math.round(rgb[0])},${Math.round(rgb[1])},${Math.round(rgb[2])})`;
}

function altColor(msl, fieldFt) {
  const field = Number.isFinite(fieldFt) ? fieldFt : 0;
  const agl = Math.max(0, (Number.isFinite(msl) ? msl : 0) - field);
  return rgbCss(lerpStops(Math.round(agl / 200) * 200, ALT_FT, ALT_RGB));
}

function paintTrailGroup(group, pts, ac, field, coarse, renderer) {
  if (!group) return;
  group.clearLayers();
  if (!pts || pts.length < 2) return;
  const layerRenderer = renderer || trailCanvas;
  for (const seg of meridianSegs(densifyTrail(pts, coarse))) {
    if (seg.length < 2) continue;
    let run = [[seg[0][0], seg[0][1]]];
    let col = altColor(((ptAlt(seg[0]) ?? 0) + (ptAlt(seg[1]) ?? 0)) / 2, field);
    const flush = () => {
      if (run.length < 2) return;
      L.polyline(run, {
        color: col,
        weight: TRAIL_WEIGHT,
        opacity: 0.92,
        lineJoin: "round",
        lineCap: "butt",
        renderer: layerRenderer,
      }).addTo(group);
    };
    for (let i = 1; i < seg.length; i += 1) {
      const nextCol = altColor(((ptAlt(seg[i - 1]) ?? 0) + (ptAlt(seg[i]) ?? 0)) / 2, field);
      if (nextCol !== col) {
        run.push([seg[i][0], seg[i][1]]);
        flush();
        col = nextCol;
        run = [[seg[i][0], seg[i][1]]];
      } else {
        run.push([seg[i][0], seg[i][1]]);
      }
    }
    flush();
  }
}

function liveSigOf(pts) {
  if (!pts || !pts.length) return "0";
  const n = pts.length;
  const a = pts[0];
  const b = n > 2 ? pts[n - 2] : a;
  return `${n}:${a[0]},${a[1]}:${b[0]},${b[1]}`;
}

// TEMP(DEBUG): recon/live join marker — remove DEBUG_TRAIL_JOIN block when done.
const DEBUG_TRAIL_JOIN = true;
let trailJoinDebugMarker = null;
const trafficJoinMarkers = new Map();
let trafficJoinRun = 0;

function clearTrafficJoinMarkers() {
  for (const layer of trafficJoinMarkers.values()) map.removeLayer(layer);
  trafficJoinMarkers.clear();
}

async function refreshTrafficJoinMarkers(aircraft) {
  if (!DEBUG_TRAIL_JOIN) {
    clearTrafficJoinMarkers();
    return;
  }
  const run = ++trafficJoinRun;
  const want = new Set();
  const cap = TOUCH_UI ? 12 : 20;
  let n = 0;
  for (const ac of aircraft || []) {
    if (n >= cap) break;
    if (!ac || !ac.id || ac.isOwnship || !ac.trackReady || !ac.trackAirborne) continue;
    if (ac.id === trailAcId) continue;
    if (typeof mapVisible === "function" && !mapVisible(ac)) continue;
    n += 1;
    want.add(ac.id);
    if (typeof fetchTrack !== "function") continue;
    const tk = await fetchTrack(ac.id, false);
    if (run !== trafficJoinRun) return;
    const join = tk.reconFrom || null;
    if (!join || !Number.isFinite(join.lat) || !Number.isFinite(join.lon)) continue;
    const latLng = [join.lat, join.lon];
    let layer = trafficJoinMarkers.get(ac.id);
    if (!layer) {
      layer = L.circleMarker(latLng, {
        radius: 5,
        color: "#ffffff",
        weight: 1,
        fillColor: "#dc2626",
        fillOpacity: 0.92,
        interactive: false,
        zIndexOffset: 2400,
      }).addTo(map);
      trafficJoinMarkers.set(ac.id, layer);
    } else {
      layer.setLatLng(latLng);
    }
  }
  if (run !== trafficJoinRun) return;
  for (const [id, layer] of trafficJoinMarkers) {
    if (!want.has(id)) {
      map.removeLayer(layer);
      trafficJoinMarkers.delete(id);
    }
  }
}

function clearTrailJoinDebug() {
  if (!trailJoinDebugMarker) return;
  map.removeLayer(trailJoinDebugMarker);
  trailJoinDebugMarker = null;
}

function setTrailJoinDebug(pt) {
  if (!DEBUG_TRAIL_JOIN) {
    clearTrailJoinDebug();
    return;
  }
  if (!pt || !Number.isFinite(pt[0]) || !Number.isFinite(pt[1])) {
    clearTrailJoinDebug();
    return;
  }
  const latLng = [pt[0], pt[1]];
  if (!trailJoinDebugMarker) {
    trailJoinDebugMarker = L.circleMarker(latLng, {
      radius: 7,
      color: "#ffffff",
      weight: 2,
      fillColor: "#dc2626",
      fillOpacity: 1,
      interactive: false,
      zIndexOffset: 2500,
    }).addTo(map);
  } else {
    trailJoinDebugMarker.setLatLng(latLng);
  }
}

function setTrail(reconPts, livePts, ac, reconKey, joinPt) {
  const recon = reconPts || [];
  const live = livePts || [];
  setTrailJoinDebug(joinPt);
  const field = fieldElevOf(ac, live.length ? live : recon);
  const key = reconKey || "";
  const sig = liveSigOf(live);
  const reconChanged = paintedReconKey !== key;
  const liveChanged = paintedLiveSig !== sig;
  if (!reconChanged && !liveChanged) return;
  if (reconChanged) {
    paintTrailGroup(trail, recon, ac, field, false, trailCanvas);
    paintedReconKey = key;
  }
  if (reconChanged || liveChanged) {
    const liveRenderer = typeof trailLiveCanvas !== "undefined" ? trailLiveCanvas : trailCanvas;
    paintTrailGroup(trailLiveGroup, live, ac, field, !ac.isOwnship, liveRenderer);
    paintedLiveSig = sig;
  }
}

function hideHistory() {
  trail.clearLayers();
  if (typeof trailLiveGroup !== "undefined") trailLiveGroup.clearLayers();
  if (typeof trailLive !== "undefined") trailLive.setLatLngs([]);
  clearTrailJoinDebug();
  paintedReconKey = null;
  paintedLiveSig = "";
  trailAcId = null;
}

function armTrail(ac) {
  if (!ac || !ac.id) return;
  if (trailAcId !== ac.id) {
    trail.clearLayers();
    if (typeof trailLiveGroup !== "undefined") trailLiveGroup.clearLayers();
    if (typeof trailLive !== "undefined") trailLive.setLatLngs([]);
    clearTrailJoinDebug();
    paintedReconKey = null;
    paintedLiveSig = "";
    histSeq += 1;
    trailAcId = ac.id;
    lastHistAt = 0;
  }
  showHistory(ac);
}
