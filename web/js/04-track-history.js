function plausibleFieldAlt(ac, alt) {
  if (!Number.isFinite(alt) || alt < -800 || alt > 14500) return false;
  const gs = Number(ac && ac.gs);
  if (Number.isFinite(gs) && gs > 50) return false;
  return true;
}

function noteLocalTrack(ac) {
  if (!ac || !ac.id) return;
  const alt = Number(ac.alt);
  const here = [ac.lat, ac.lon, Number.isFinite(alt) ? alt : 0];
  if (ac.onGround && plausibleFieldAlt(ac, alt) && !localFieldAlt.has(ac.id)) {
    localFieldAlt.set(ac.id, alt);
  }
  let buf = localTracks.get(ac.id);
  if (!buf) {
    buf = [here];
    localTracks.set(ac.id, buf);
    return;
  }
  const last = buf[buf.length - 1];
  const moved = kmBetween(last, here);
  if (moved == null) return;
  if (moved > 80) {
    buf.length = 0;
    buf.push(here);
    localFieldAlt.delete(ac.id);
    reconById.delete(ac.id);
    if (ac.onGround && Number.isFinite(alt)) localFieldAlt.set(ac.id, alt);
    return;
  }
  const gs = Number(ac.gs) || 0;
  const maxKm = Math.max(12, (gs * 1.852 * 25) / 3600 * 1.8);
  if (moved > maxKm) return;
  const own = Boolean(ac.isOwnship);
  if (!own) {
    const dAlt = Math.abs((Number.isFinite(alt) ? alt : 0) - (Number(last[2]) || 0));
    let dHdg = 0;
    if (buf.length >= 2) {
      const prev = buf[buf.length - 2];
      const from = bearingTo(prev, last);
      const to = bearingTo(last, here);
      dHdg = Math.abs(from - to) % 360;
      if (dHdg > 180) dHdg = 360 - dHdg;
    }
    const term = Boolean(ac.onGround) || gs < 250 || (Number.isFinite(alt) && alt < 12000);
    const needKm = term ? 0.5 : 3;
    if (moved < needKm && dHdg < 4 && dAlt < 800) {
      if (buf.length === 1) {
        if (moved > 0.03) buf.push(here);
        return;
      }
      const fromPrev = kmBetween(buf[buf.length - 2], here);
      if (fromPrev != null && fromPrev >= needKm) {
        buf.push(here);
        return;
      }
      last[0] = here[0];
      last[1] = here[1];
      last[2] = here[2];
      return;
    }
  } else if (moved < 0.03) {
    last[0] = here[0];
    last[1] = here[1];
    last[2] = here[2];
    return;
  }
  buf.push(here);
  const watching = ac.id === trailAcId;
  const cap = own ? LOCAL_TRACK_MAX : watching ? (TOUCH_UI ? 160 : 400) : TOUCH_UI ? 80 : 180;
  if (buf.length > cap) buf.splice(1, buf.length - cap);
}

const trackFetchInflight = new Map();

function forgetTrack(id) {
  localTracks.delete(id);
  localFieldAlt.delete(id);
  trackCache.delete(id);
  reconById.delete(id);
  trackFetchInflight.delete(id);
}

function forgetAllTracks() {
  localTracks.clear();
  localFieldAlt.clear();
  trackCache.clear();
  reconById.clear();
  trackFetchInflight.clear();
  lastFlightLegs.clear();
}

function copyTrackPt(p) {
  return [p[0], p[1], Number.isFinite(Number(p[2])) ? Number(p[2]) : 0];
}

function absorbTrackPts(id, pts) {
  if (!id || !pts || pts.length < 2) return;
  const have = localTracks.get(id);
  if (!have || have.length <= 2) {
    localTracks.set(id, pts.map(copyTrackPt));
    return;
  }
  const merged = mergeTrackPts(pts, have, have[have.length - 1]);
  if (merged.length > have.length) localTracks.set(id, merged);
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
        sid: data.sidPath || [],
        star: data.starPath || [],
        at: Date.now(),
      };
      trackCache.set(id, next);
      absorbTrackPts(id, next.pts);
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
      return hit || { pts: [], first: null, sid: [], star: [] };
    } finally {
      trackFetchInflight.delete(id);
    }
  })();
  trackFetchInflight.set(id, req);
  return req;
}

function prefetchVisibleTracks(aircraft) {
  let n = 0;
  for (const ac of aircraft || []) {
    if (n >= 3) break;
    if (!ac || !ac.id || ac.isOwnship) continue;
    if (trackCache.has(ac.id) || trackFetchInflight.has(ac.id)) continue;
    if (typeof mapVisible === "function" && !mapVisible(ac)) continue;
    n += 1;
    fetchTrack(ac.id, false);
  }
}

function dropTeleports(pts, here) {
  // Sparse BATC AI can sit 80–150 km between breadcrumbs because the
  // server slides the tail. That is the flown path, not a teleport.
  const jumpKm = 250;
  const out = [];
  for (const p of pts || []) {
    if (out.length && kmBetween(out[out.length - 1], p) > jumpKm) out.length = 0;
    out.push(p);
  }
  if (out.length && here && kmBetween(out[out.length - 1], here) > jumpKm) return [];
  return out;
}

function mergeTrackPts(server, local, here) {
  const copy = (p) => [p[0], p[1], Number.isFinite(Number(p[2])) ? Number(p[2]) : null];
  const a = (server || []).map(copy);
  const b = (local || []).map(copy);
  if (!a.length) return dropTeleports(appendHere(b, here), here);
  if (!b.length) return dropTeleports(appendHere(a, here), here);
  const last = a[a.length - 1];
  let best = 0;
  let bestD = kmBetween(b[0], last);
  for (let k = 1; k < b.length; k += 1) {
    const d = kmBetween(b[k], last);
    if (d < bestD) {
      bestD = d;
      best = k;
    }
  }
  if (bestD > 15) {
    // Never drop server history for a short local buffer that started on click.
    return dropTeleports(appendHere(a.slice(), here), here);
  }
  const skipDup = bestD < 0.05 ? 1 : 0;
  return dropTeleports(a.concat(b.slice(best + skipDup)), here);
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

function flightKeyOf(ac) {
  return [
    ac.id,
    ac.origin || "",
    ac.dest || "",
    ac.sid || "",
    ac.star || "",
    ac.depRunway || "",
    ac.arrRunway || "",
  ].join("|");
}

function headingFromLive(live) {
  if (!live || live.length < 2) return null;
  const a = live[0];
  const b = live.find((p) => kmBetween(a, p) > 0.3);
  return b ? bearingTo(a, b) : null;
}

/**
 * Freeze point: first sim observation of this leg.
 * Reconstruction ends here. The live trail starts here and never goes back into recon.
 */
function spawnOf(first, live) {
  const liveHdg = headingFromLive(live);
  if (first && Number.isFinite(Number(first.lat)) && Number.isFinite(Number(first.lon))) {
    const hdg = liveHdg != null ? liveHdg : Number(first.hdg);
    const alt = Number(first.alt);
    return {
      pt: [Number(first.lat), Number(first.lon), Number.isFinite(alt) ? alt : ptAlt(live && live[0])],
      hdg: Number.isFinite(hdg) ? hdg : null,
      onGround: Boolean(first.onGround),
    };
  }
  const head = live && live[0];
  if (!head) return null;
  return { pt: head.slice(), hdg: liveHdg, onGround: false };
}

function departStartOf(ac) {
  if (ac.depRwyLat != null && ac.depRwyLon != null) return [ac.depRwyLat, ac.depRwyLon];
  return originLL(ac);
}

function needsRecon(ac, spawn) {
  if (!ac || !spawn || ac.isOwnship || ac.source === "demo") return false;
  const start = departStartOf(ac) || originLL(ac);
  if (!start) return false;
  return (kmBetween(start, spawn.pt) || 0) >= 8;
}

function pastProcedure(path, origin, spawn) {
  if (!path || path.length < 2 || !origin || !spawn) return false;
  const end = path[path.length - 1];
  return (kmBetween(origin, spawn) || 0) > (kmBetween(origin, end) || 0) - 4;
}

function headingDiff(a, b) {
  let d = Math.abs(Number(a) - Number(b)) % 360;
  if (d > 180) d = 360 - d;
  return d;
}

/** Procedure vertices behind the spawn; never a lateral snap onto the line. */
function clipProcToSpawn(path, spawn, hdg) {
  if (!path || path.length < 2 || !spawn) return [];
  const hit = nearestOnPath(path, spawn);
  if (hit.d >= 12) return [];
  const out = path.slice(0, hit.i + 1);
  const nxt = path[hit.i + 1];
  const along = nxt ? bearingTo(path[hit.i], nxt) : null;
  const onLine = hit.t > 0.02 && hit.d < 0.5 && along != null && Number.isFinite(hdg) && headingDiff(along, hdg) < 40;
  if (onLine) out.push(hit.pt);
  if (Number.isFinite(hdg)) {
    while (out.length >= 2 && headingDiff(bearingTo(out[out.length - 1], spawn), hdg) > 70) {
      out.pop();
    }
  }
  return out;
}

function departurePart(ac, spawn, sid, hdg) {
  const start = departStartOf(ac);
  const origin = originLL(ac) || start;
  if (sid && sid.length > 1) {
    const hit = nearestOnPath(sid, spawn);
    if (hit.d < 12) return clipProcToSpawn(sid, spawn, hdg);
    if (pastProcedure(sid, origin, spawn)) return sid.map((p) => p.slice());
  }
  const stub = noSidDepart(ac, spawn);
  if (stub.length) return stub;
  return start ? [start.slice()] : [];
}

function joinToSpawn(pts, spawn) {
  const a = spawn && spawn.pt;
  if (!a) return;
  if (!pts.length) {
    pts.push(a.slice());
    return;
  }
  const gap = kmBetween(pts[pts.length - 1], a);
  if (gap == null) return;
  if (gap <= 12) appendPathPts(pts, [a]);
  else appendGcTo(pts, a);
  pts[pts.length - 1] = a.slice();
}

function buildRecon(ac, spawn, sid, star) {
  const a = spawn.pt;
  let pts = departurePart(ac, a, sid, spawn.hdg);
  // Common STAR only (no enroute transitions). If the aircraft is already
  // off the procedure, skip it and great-circle from the SID to spawn.
  const arrival = starFlownTo(star, a, destLL(ac), spawn.hdg);
  if (arrival.length) {
    if (pts.length) appendGcTo(pts, arrival[0]);
    appendPathPts(pts, pts.length ? arrival.slice(1) : arrival);
  }
  joinToSpawn(pts, spawn);
  pts[pts.length - 1] = a.slice();
  if (pts.length > 2) pts = smoothPath(pts, ac);
  pts[pts.length - 1] = a.slice();
  if (ptAlt(a) != null) pts[pts.length - 1][2] = ptAlt(a);
  return pts;
}

function reconFor(ac, spawn, sid, star) {
  if (!needsRecon(ac, spawn)) {
    reconById.delete(ac.id);
    return null;
  }
  const a = spawn.pt;
  const key = `${flightKeyOf(ac)}|${a[0].toFixed(3)},${a[1].toFixed(3)}|${(sid || []).length}|${(star || []).length}`;
  const hit = reconById.get(ac.id);
  if (hit && hit.key === key) return hit;
  const next = { key, pts: buildRecon(ac, spawn, sid || [], star || []) };
  reconById.set(ac.id, next);
  return next;
}

let historyRun = 0;

async function showHistory(ac) {
  if (!ac) return;
  const run = ++historyRun;
  const watching = ac.id;
  const seq = histSeq;
  const hereAlt = Number(ac.alt);
  const latest = typeof motionById !== "undefined" ? motionById.get(ac.id)?.samples?.slice(-1)[0] : null;
  const here =
    latest && Number.isFinite(latest.lat) && Number.isFinite(latest.lon)
      ? [latest.lat, latest.lon, Number.isFinite(latest.alt) ? latest.alt : Number.isFinite(hereAlt) ? hereAlt : null]
      : [ac.lat, ac.lon, Number.isFinite(hereAlt) ? hereAlt : null];
  const fetched = await fetchTrack(ac.id, Boolean(ac.isOwnship));
  if (run !== historyRun || trailAcId !== watching || seq !== histSeq) return;
  const live = mergeTrackPts(fetched.pts.slice(), localTracks.get(ac.id) || [], here);
  if (!live.length) live.push(here.slice());
  const spawn = spawnOf(fetched.first, live);
  const recon = reconFor(ac, spawn, fetched.sid, fetched.star);

  let livePts = [];
  for (const p of live) {
    if (livePts.length && kmBetween(livePts[livePts.length - 1], p) < 0.04) continue;
    livePts.push(p);
  }
  if (recon && recon.pts.length && livePts.length) {
    livePts[0] = recon.pts[recon.pts.length - 1].slice();
  }
  if (typeof clipPtsBehindPose === "function") livePts = clipPtsBehindPose(livePts, ac);
  const tip =
    typeof displayedPoseOf === "function" ? displayedPoseOf(ac) : null;
  const tipPt =
    tip && Number.isFinite(tip.lat) && Number.isFinite(tip.lon)
      ? [tip.lat, tip.lon, Number.isFinite(tip.alt) ? tip.alt : here[2]]
      : here;
  appendHere(livePts, tipPt);
  const reconPts = recon && recon.pts.length ? recon.pts : null;
  if (reconPts && livePts.length) {
    const combined = reconPts.concat(livePts.slice(1));
    assignTrailAlts(combined, ac, reconPts.length);
    for (let i = 0; i < reconPts.length; i += 1) reconPts[i] = combined[i];
    livePts = combined.slice(reconPts.length - 1);
  } else {
    assignTrailAlts(livePts, ac, 0);
  }
  setTrail(reconPts || [], livePts, ac, recon ? recon.key : `live:${ac.id}`);
}

function nearestOnPath(path, here) {
  const miss = { d: Infinity, i: 0, t: 0, frac: 1, pt: here };
  if (!path || !path.length || !here) return miss;
  let best = { d: Infinity, i: 0, t: 0, frac: 0, pt: path[0] };
  const n = Math.max(1, path.length - 1);
  for (let i = 0; i < path.length; i += 1) {
    const d = kmBetween(path[i], here);
    if (d < best.d) best = { d, i, t: 0, frac: i / n, pt: path[i] };
    if (i + 1 >= path.length) continue;
    const hit = projectOnSeg(path[i], path[i + 1], here);
    if (hit.d < best.d) best = { d: hit.d, i, t: hit.t, frac: (i + hit.t) / n, pt: hit.pt };
  }
  return best;
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

function clipToHit(path, hit, here) {
  if (!path.length) return [];
  const out = path.slice(0, hit.i + 1);
  if (hit.t > 0.02) out.push(hit.pt);
  if (!here) return out;
  if (!out.length || kmBetween(out[out.length - 1], here) > 0.05) out.push(here);
  return out;
}

function appendPathPts(pts, extra) {
  for (const p of extra) {
    if (!p) continue;
    if (!pts.length) {
      pts.push(p.slice());
      continue;
    }
    if (kmBetween(pts[pts.length - 1], p) < 0.04) continue;
    pts.push(p.slice());
  }
}

function appendGcTo(pts, to) {
  if (!pts.length || !to) return false;
  if (kmBetween(pts[pts.length - 1], to) <= 12) {
    appendPathPts(pts, [to]);
    return false;
  }
  const gc = gcPoints(pts[pts.length - 1], to);
  for (let i = 1; i < gc.length; i += 1) pts.push(gc[i]);
  return true;
}

function starFlownTo(star, spawn, dest, hdg) {
  if (!star || star.length < 2 || !spawn) return [];
  const out = clipProcToSpawn(star, spawn, hdg);
  if (dest && out.length >= 2) {
    const toDest = kmBetween(spawn, dest);
    if (Number.isFinite(toDest)) {
      while (out.length >= 2 && kmBetween(out[out.length - 1], dest) + 0.4 < toDest) {
        out.pop();
      }
    }
  }
  return out;
}

function turnRadiusKm(kt, bankDeg) {
  const v = Math.max(80, kt) * 0.514444;
  const tanB = Math.tan((bankDeg * Math.PI) / 180);
  return (v * v) / (9.80665 * tanB) / 1000;
}

function speedForTurn(ac, alongKm) {
  if (alongKm < 12) return 185;
  if (alongKm < 35) return 220;
  if (alongKm < 90) return 250;
  if (alongKm < 250) return 340;
  return 420;
}

function toXY(p, o) {
  const k = 111.32;
  return [(p[1] - o[1]) * k * Math.cos((o[0] * Math.PI) / 180), (p[0] - o[0]) * k];
}

function fromXY(xy, o) {
  const k = 111.32;
  return [o[0] + xy[1] / k, o[1] + xy[0] / (k * Math.cos((o[0] * Math.PI) / 180))];
}

function unit(vx, vy) {
  const n = Math.hypot(vx, vy) || 1;
  return [vx / n, vy / n];
}

function courseDelta(u, v) {
  const cr = u[0] * v[1] - u[1] * v[0];
  const dt = Math.max(-1, Math.min(1, u[0] * v[0] + u[1] * v[1]));
  const ang = Math.acos(dt);
  return { ang, left: cr > 0 };
}

function easeSharpCorners(pts) {
  if (pts.length < 3) return pts;
  const out = [pts[0]];
  for (let i = 1; i < pts.length - 1; i += 1) {
    const prev = pts[i - 1];
    const cur = pts[i];
    const next = pts[i + 1];
    const d1 = kmBetween(prev, cur);
    const d2 = kmBetween(cur, next);
    out.push(cur);
    if (d1 < 1 || d2 < 1 || Math.abs(cur[0]) > 72) continue;
    const a = toXY(prev, cur);
    const b = toXY(next, cur);
    const u = unit(-a[0], -a[1]);
    const v = unit(b[0], b[1]);
    const { ang, left } = courseDelta(u, v);
    if ((ang * 180) / Math.PI < 110) continue;
    const L = Math.max(3, Math.min(22, Math.min(d1, d2) * 0.4));
    const n = left ? [-u[1], u[0]] : [u[1], -u[0]];
    const w1 = [u[0] * 0.5 * L + n[0] * 0.6 * L, u[1] * 0.5 * L + n[1] * 0.6 * L];
    const w2 = [-u[0] * 0.3 * L + n[0] * 1.6 * L, -u[1] * 0.3 * L + n[1] * 1.6 * L];
    out.push(fromXY(w1, cur), fromXY(w2, cur));
  }
  out.push(pts[pts.length - 1]);
  return out;
}

function smoothPath(pts, ac) {
  pts = easeSharpCorners(pts);
  if (pts.length < 3) return pts;
  const out = [pts[0]];
  let along = 0;
  for (let i = 1; i < pts.length - 1; i += 1) {
    const prev = pts[i - 1];
    const cur = pts[i];
    const next = pts[i + 1];
    const d1 = kmBetween(prev, cur);
    const d2 = kmBetween(cur, next);
    along += d1;
    if (d1 < 0.35 || d2 < 0.35 || Math.abs(cur[0]) > 72) {
      out.push(cur);
      continue;
    }
    const o = cur;
    const a = toXY(prev, o);
    const b = toXY(next, o);
    const u = unit(-a[0], -a[1]);
    const v = unit(b[0], b[1]);
    const { ang, left } = courseDelta(u, v);
    const deg = (ang * 180) / Math.PI;
    if (deg < 7 || deg > 178) {
      out.push(cur);
      continue;
    }
    let R = turnRadiusKm(speedForTurn(ac, along), 25);
    if (deg > 40) R = Math.max(R, 5);
    const half = Math.tan(ang / 2);
    if (half < 1e-3) {
      out.push(cur);
      continue;
    }
    let lead = R * half;
    const cap = Math.min(d1, d2) * 0.48;
    if (lead > cap) {
      lead = cap;
      R = lead / half;
    }
    if (R < 0.35 || lead < 0.2) {
      out.push(cur);
      continue;
    }
    const A = [-u[0] * lead, -u[1] * lead];
    const Bxy = [v[0] * lead, v[1] * lead];
    const n = left ? [-u[1], u[0]] : [u[1], -u[0]];
    const Ctr = [A[0] + n[0] * R, A[1] + n[1] * R];
    const sAng = Math.atan2(A[1] - Ctr[1], A[0] - Ctr[0]);
    const eAng = Math.atan2(Bxy[1] - Ctr[1], Bxy[0] - Ctr[0]);
    let sweep = eAng - sAng;
    if (left && sweep < 0) sweep += 2 * Math.PI;
    if (!left && sweep > 0) sweep -= 2 * Math.PI;
    if (Math.abs(sweep) < 0.05) {
      out.push(cur);
      continue;
    }
    const steps = Math.max(4, Math.min(18, Math.ceil((R * Math.abs(sweep)) / 0.7)));
    out.push(fromXY(A, o));
    for (let s = 1; s < steps; s += 1) {
      const t = s / steps;
      const an = sAng + sweep * t;
      out.push(fromXY([Ctr[0] + Math.cos(an) * R, Ctr[1] + Math.sin(an) * R], o));
    }
    out.push(fromXY(Bxy, o));
  }
  out.push(pts[pts.length - 1]);
  return out;
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

