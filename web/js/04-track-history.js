function plausibleFieldAlt(ac, alt) {
  if (!Number.isFinite(alt) || alt < -800 || alt > 14500) return false;
  const gs = Number(ac && ac.gs);
  if (Number.isFinite(gs) && gs > 50) return false;
  return true;
}

function noteFieldAltFromAc(ac) {
  if (!ac || !ac.id) return;
  const alt = Number(ac.alt);
  if (ac.onGround && plausibleFieldAlt(ac, alt) && !localFieldAlt.has(ac.id)) {
    localFieldAlt.set(ac.id, alt);
  }
}

const trackFetchInflight = new Map();

function forgetTrack(id) {
  localFieldAlt.delete(id);
  trackCache.delete(id);
  trackFetchInflight.delete(id);
}

function forgetAllTracks() {
  localFieldAlt.clear();
  trackCache.clear();
  trackFetchInflight.clear();
  lastFlightLegs.clear();
}

const lastFlightLegs = new Map();

function noteFlightLeg(ac) {
  if (!ac || !ac.id) return false;
  const origin = (ac.origin || "").toUpperCase();
  const dest = (ac.dest || "").toUpperCase();
  const prev = lastFlightLegs.get(ac.id);
  if (origin || dest) {
    const po = prev ? prev.origin : "";
    const pd = prev ? prev.dest : "";
    lastFlightLegs.set(ac.id, { origin: origin || po, dest: dest || pd });
  }
  if (prev && dest && prev.dest && dest !== prev.dest) {
    forgetTrack(ac.id);
    return true;
  }
  return false;
}

async function fetchTrack(id, ownship) {
  const hit = trackCache.get(id);
  const cacheMs = ownship ? (TOUCH_UI ? 2800 : 900) : TOUCH_UI ? 4000 : 2500;
  if (hit && Date.now() - hit.at < cacheMs) return hit;
  const pending = trackFetchInflight.get(id);
  if (pending) return pending;
  const req = (async () => {
    try {
      const res = await fetch(`/api/track?id=${encodeURIComponent(id)}`, { cache: "no-store" });
      const data = await res.json();
      const next = {
        pts: data.points || [],
        first: data.firstSeen || null,
        reconFrom: data.reconFrom || null,
        reconCount: Number(data.reconCount) || 0,
        at: Date.now(),
      };
      trackCache.set(id, next);
      if (TOUCH_UI && trackCache.size > 4) {
        let oldestId = id;
        let oldestAt = next.at;
        for (const [key, val] of trackCache) {
          if (val.at < oldestAt) {
            oldestAt = val.at;
            oldestId = key;
          }
        }
        if (oldestId !== id) trackCache.delete(oldestId);
      }
      return next;
    } catch {
      return hit || { pts: [], first: null, reconFrom: null, reconCount: 0 };
    } finally {
      trackFetchInflight.delete(id);
    }
  })();
  trackFetchInflight.set(id, req);
  return req;
}

function prefetchVisibleTracks(aircraft) {
  let n = 0;
  const cap = TOUCH_UI ? 8 : 14;
  for (const ac of aircraft || []) {
    if (n >= cap) break;
    if (!ac || !ac.id || ac.isOwnship || !ac.trackReady) continue;
    if (trackCache.has(ac.id) || trackFetchInflight.has(ac.id)) continue;
    if (typeof mapVisible === "function" && !mapVisible(ac)) continue;
    n += 1;
    fetchTrack(ac.id, false);
  }
}

function appendHere(pts, here) {
  if (!here) return pts;
  const next = [here[0], here[1], Number.isFinite(Number(here[2])) ? Number(here[2]) : null];
  if (!pts.length) {
    pts.push(next);
    return pts;
  }
  const last = pts[pts.length - 1];
  if (kmBetween(last, next) < 0.03) {
    last[0] = next[0];
    last[1] = next[1];
    if (next[2] != null) last[2] = next[2];
  } else pts.push(next);
  return pts;
}

function slerpGc(a, b) {
  const d = kmBetween(a, b);
  if (!a || !b || d == null) return [];
  if (d < 3) return [a.slice(), b.slice()];
  const lat1 = (a[0] * Math.PI) / 180;
  const lon1 = (a[1] * Math.PI) / 180;
  const lat2 = (b[0] * Math.PI) / 180;
  const lon2 = (b[1] * Math.PI) / 180;
  const x1 = Math.cos(lat1) * Math.cos(lon1);
  const y1 = Math.cos(lat1) * Math.sin(lon1);
  const z1 = Math.sin(lat1);
  const x2 = Math.cos(lat2) * Math.cos(lon2);
  const y2 = Math.cos(lat2) * Math.sin(lon2);
  const z2 = Math.sin(lat2);
  const dot = Math.max(-1, Math.min(1, x1 * x2 + y1 * y2 + z1 * z2));
  const om = Math.acos(dot);
  if (om < 1e-6) return [a.slice(), b.slice()];
  const step = d > 4000 ? 90 : d > 1500 ? 55 : 28;
  const n = Math.max(2, Math.min(160, Math.ceil(d / step)));
  const out = [];
  const sinOm = Math.sin(om);
  for (let i = 0; i <= n; i += 1) {
    const t = i / n;
    const s1 = Math.sin((1 - t) * om) / sinOm;
    const s2 = Math.sin(t * om) / sinOm;
    const x = s1 * x1 + s2 * x2;
    const y = s1 * y1 + s2 * y2;
    const z = s1 * z1 + s2 * z2;
    const lat = (Math.atan2(z, Math.hypot(x, y)) * 180) / Math.PI;
    let lon = (Math.atan2(y, x) * 180) / Math.PI;
    if (out.length) {
      const prev = out[out.length - 1][1];
      while (lon - prev > 180) lon -= 360;
      while (lon - prev < -180) lon += 360;
    }
    out.push([lat, lon]);
  }
  return out;
}

function gcPeak(pts) {
  let peak = pts[0];
  for (const p of pts) {
    if (Math.abs(p[0]) > Math.abs(peak[0])) peak = p;
  }
  return peak;
}

function gcPoints(a, b, depth) {
  const pts = slerpGc(a, b);
  if (pts.length < 2) return pts;
  const cap = 70;
  const peak = gcPeak(pts);
  if (Math.abs(peak[0]) <= cap || (depth || 0) > 3) return pts;
  const way = [(peak[0] < 0 ? -cap : cap), peak[1]];
  if (kmBetween(a, way) < 40 || kmBetween(way, b) < 40) return pts;
  const left = gcPoints(a, way, (depth || 0) + 1);
  const right = gcPoints(way, b, (depth || 0) + 1);
  return left.concat(right.slice(1));
}

/** Great-circle polyline from destination to current position (direct-to line). */
function directDestRoutePts(dest, lat, lon) {
  return gcPoints(dest, [lat, lon]);
}

function headingDiff(a, b) {
  let d = Math.abs(Number(a) - Number(b)) % 360;
  if (d > 180) d = 360 - d;
  return d;
}

let historyRun = 0;

function historyHere(ac) {
  const hereAlt = Number(ac.alt);
  const latest = typeof motionById !== "undefined" ? motionById.get(ac.id)?.samples?.slice(-1)[0] : null;
  if (latest && Number.isFinite(latest.lat) && Number.isFinite(latest.lon)) {
    return [
      latest.lat,
      latest.lon,
      Number.isFinite(latest.alt) ? latest.alt : Number.isFinite(hereAlt) ? hereAlt : null,
    ];
  }
  return [ac.lat, ac.lon, Number.isFinite(hereAlt) ? hereAlt : null];
}

function paintHistoryFrom(ac, fetched, here) {
  const rc = Math.max(0, Number(fetched.reconCount) || 0);
  const raw = (fetched.pts || []).map((p) => [p[0], p[1], p.length > 2 ? p[2] : null]);
  const reconPts = rc > 1 ? raw.slice(0, rc).map((p) => p.slice()) : [];
  let livePts = (rc > 0 ? raw.slice(Math.max(0, rc - 1)) : raw).map((p) => p.slice());
  if (!livePts.length) livePts.push(here.slice());
  const deduped = [];
  for (const p of livePts) {
    if (deduped.length && kmBetween(deduped[deduped.length - 1], p) < 0.04) continue;
    deduped.push(p);
  }
  livePts = deduped;
  if (reconPts.length && livePts.length) livePts[0] = reconPts[reconPts.length - 1].slice();
  if (typeof clipPtsBehindPose === "function") livePts = clipPtsBehindPose(livePts, ac);
  const tip = typeof displayedPoseOf === "function" ? displayedPoseOf(ac) : null;
  const tipPt =
    tip && Number.isFinite(tip.lat) && Number.isFinite(tip.lon)
      ? [tip.lat, tip.lon, Number.isFinite(tip.alt) ? tip.alt : here[2]]
      : here;
  appendHere(livePts, tipPt);
  const reconCount = reconPts.length;
  const combined = reconCount ? reconPts.concat(livePts.slice(1)) : livePts;
  assignTrailAlts(combined, ac, reconCount);
  const reconOut = reconCount ? combined.slice(0, reconCount) : [];
  const liveOut = combined.slice(reconCount ? reconCount - 1 : 0);
  const joinPt = reconOut.length
    ? reconOut[reconOut.length - 1].slice()
    : liveOut[0]
      ? liveOut[0].slice()
      : null;
  const head = reconOut[0];
  const tail = reconOut.length ? reconOut[reconOut.length - 1] : null;
  const pathKey = tail
    ? `${ac.id}:${reconOut.length}:${head[0]},${head[1]}:${tail[0]},${tail[1]}`
    : `${ac.id}:0`;
  setTrail(reconOut, liveOut, ac, pathKey, joinPt);
}

async function showHistory(ac) {
  if (!ac) return;
  const run = ++historyRun;
  const watching = ac.id;
  const seq = histSeq;
  const here = historyHere(ac);
  const fetched = await fetchTrack(ac.id, Boolean(ac.isOwnship));
  if (run !== historyRun || trailAcId !== watching || seq !== histSeq) return;
  paintHistoryFrom(ac, fetched, here);
}

function projectOnSeg(a, b, p) {
  const x1 = a[1];
  const y1 = a[0];
  const x2 = b[1];
  const y2 = b[0];
  const dx = x2 - x1;
  const dy = y2 - y1;
  const den = dx * dx + dy * dy;
  let t = 0;
  if (den > 1e-12) t = ((p[1] - x1) * dx + (p[0] - y1) * dy) / den;
  t = Math.max(0, Math.min(1, t));
  const pt = [y1 + dy * t, x1 + dx * t];
  return { t, pt, d: kmBetween(pt, p) };
}

function unwrapPts(pts) {
  const out = [];
  for (const p of pts) {
    let lon = p[1];
    if (out.length) {
      const prev = out[out.length - 1][1];
      while (lon - prev > 180) lon -= 360;
      while (lon - prev < -180) lon += 360;
    }
    const row = [p[0], lon];
    if (p.length > 2) row[2] = p[2];
    out.push(row);
  }
  return out;
}

function meridianSegs(pts) {
  const segs = [];
  let cur = [];
  for (const p of unwrapPts(pts)) {
    const wrapped = ((((p[1] + 180) % 360) + 360) % 360) - 180;
    if (cur.length && Math.abs(wrapped - cur[cur.length - 1][1]) > 170) {
      segs.push(cur);
      cur = [];
    }
    const row = [p[0], wrapped];
    if (p.length > 2) row[2] = p[2];
    cur.push(row);
  }
  if (cur.length) segs.push(cur);
  return segs;
}

function ptAlt(p) {
  const a = Number(p && p[2]);
  return Number.isFinite(a) ? a : null;
}

function copyPt(p, t, b) {
  const altA = ptAlt(p);
  const altB = ptAlt(b);
  const alt = altA != null && altB != null ? altA + (altB - altA) * t : altB ?? altA;
  return [p[0] + (b[0] - p[0]) * t, p[1] + (b[1] - p[1]) * t, alt];
}

function densifyTrail(pts, coarse) {
  if (!pts || pts.length < 2) return pts || [];
  const dk = coarse ? (TOUCH_UI ? 40 : 22) : TOUCH_UI ? 28 : 14;
  const daStep = coarse ? (TOUCH_UI ? 4800 : 2400) : TOUCH_UI ? 3600 : 1800;
  const cap = coarse ? (TOUCH_UI ? 6 : 10) : TOUCH_UI ? 10 : 20;
  const out = [pts[0].slice()];
  for (let i = 1; i < pts.length; i += 1) {
    const a = out[out.length - 1];
    const b = pts[i];
    const d = kmBetween(a, b) || 0;
    const da = Math.abs((ptAlt(b) ?? ptAlt(a) ?? 0) - (ptAlt(a) ?? 0));
    const steps = Math.min(cap, Math.max(1, Math.ceil(d / dk), Math.ceil(da / daStep)));
    for (let s = 1; s <= steps; s += 1) out.push(copyPt(a, s / steps, b));
  }
  return out;
}
