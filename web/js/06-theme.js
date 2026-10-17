let hideTimer = null;
const FOLLOW_PAUSE_MS = 10000;
/** After user pan-away, catch-up larger than this uses setView so canvas trails/routes redraw. */
const FOLLOW_SNAP_PX = 72;
/**
 * Very rare safety valve: only after extreme _rawPanBy drift (px).
 * Normal follow never setViews just for pane drift — that killed phone perf.
 */
const FOLLOW_RARE_COMMIT_PX = 960;
/** Throttle full trail history rebuild on hard/rare commits. */
const FOLLOW_TRAIL_FULL_MS = 8000;
/** Debounce tile grid refresh while following (ms). */
const FOLLOW_TILES_MS = typeof TOUCH_UI !== "undefined" && TOUCH_UI ? 900 : 450;
/** Heal SVG/canvas vectors while following without setView (ms). */
const FOLLOW_VECTOR_MS = 2500;
/** Also heal vectors after this much pane drift (px). */
const FOLLOW_VECTOR_DRIFT_PX = 360;
/** Experimental follow spring frequency (Hz), critically damped. */
const FOLLOW_SPRING_HZ = 2.2;
/** Follow pan deadband (px²). */
const FOLLOW_DEADBAND2 = 0.25;
let followMe = localStorage.getItem("efb-follow-me") === "1";
/** Traffic aircraft id when following a non-ownship target; mutually exclusive with followMe. */
let followAcId = null;
let followPausedUntil = 0;
let followMoveLock = 0;
/** True after a user pause — next follow pan must commit a real map view (restore path/route). */
let followNeedsViewCommit = false;
let followSpringVx = 0;
let followSpringVy = 0;
let followSpringAt = 0;
let followTrailFullAt = 0;
let followVectorAt = 0;
let followVectorPaneMark = null;

function resetFollowSpring() {
  followSpringVx = 0;
  followSpringVy = 0;
  followSpringAt = 0;
  followVectorAt = 0;
  followVectorPaneMark = null;
}

function dampFollowSpring(factor) {
  followSpringVx *= factor;
  followSpringVy *= factor;
}

function isPhoneLayout() {
  return document.documentElement.classList.contains("layout-phone");
}

function panelInsetLeft() {
  if (isPhoneLayout()) return 0;
  const panel = document.getElementById("panel");
  const mapEl = document.getElementById("map");
  if (!panel || panel.hidden || !mapEl) return 0;
  const pr = panel.getBoundingClientRect();
  const mr = mapEl.getBoundingClientRect();
  return Math.max(0, Math.min(pr.right - mr.left, mr.width));
}

function panelInsetBottom() {
  if (!isPhoneLayout()) return 0;
  const panel = document.getElementById("panel");
  const mapEl = document.getElementById("map");
  if (!panel || panel.hidden || !mapEl) return 0;
  const pr = panel.getBoundingClientRect();
  const mr = mapEl.getBoundingClientRect();
  return Math.max(0, Math.min(mr.bottom - pr.top, mr.height));
}

/** Container point at the center of the map area not covered by the panel. */
function mapVisibleCenterContainerPoint(size) {
  const s = size || map.getSize();
  if (!s || !s.x) return null;
  const left = panelInsetLeft();
  const bottom = panelInsetBottom();
  const x = left > 0 ? left + (s.x - left) / 2 : s.x / 2;
  const y = bottom > 0 ? (s.y - bottom) / 2 : s.y / 2;
  return L.point(x, y);
}

function withFollowMoveLock(fn) {
  followMoveLock += 1;
  try {
    fn();
  } finally {
    setTimeout(() => {
      followMoveLock = Math.max(0, followMoveLock - 1);
    }, 50);
  }
}

const FIDS_ZOOM_GROUND = 17;
const FIDS_ZOOM_AIR = 13;
const FOLLOW_ZOOM_GROUND = 17;
const FOLLOW_ZOOM_AIR = 11;

function fidsZoomForAc(ac) {
  if (ac && ac.onGround) return FIDS_ZOOM_GROUND;
  return FIDS_ZOOM_AIR;
}

function followZoomForAc(ac) {
  if (ac && ac.onGround) return FOLLOW_ZOOM_GROUND;
  return FOLLOW_ZOOM_AIR;
}

/** Leaflet view center so lat/lon sits on the UI-aware visible map center. */
function mapCenterForPointAtVisibleCenter(lat, lon, zoom) {
  const z = Number.isFinite(zoom) ? zoom : map.getZoom();
  const size = map.getSize();
  const target = mapVisibleCenterContainerPoint(size);
  if (!target || !size || !size.x) return L.latLng(lat, lon);
  const half = size.divideBy(2);
  const offset = target.subtract(half);
  if (Math.abs(offset.x) < 0.5 && Math.abs(offset.y) < 0.5) return L.latLng(lat, lon);
  const pointPx = map.project([lat, lon], z);
  return map.unproject(pointPx.subtract(offset), z);
}

/** Move map so lat/lon is at the center of the map area not covered by the panel. */
function panToMapCenter(lat, lon, opts = {}) {
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;
  const animate = opts.animate === true;
  const zoom = map.getZoom();
  const center = mapCenterForPointAtVisibleCenter(lat, lon, zoom);
  const cur = map.getCenter();
  if (cur && Number.isFinite(cur.lat)) {
    const a = map.latLngToLayerPoint(cur);
    const b = map.latLngToLayerPoint(center);
    if (Math.abs(a.x - b.x) < 0.75 && Math.abs(a.y - b.y) < 0.75) return;
  }
  withFollowMoveLock(() => {
    map.setView(center, zoom, {
      animate,
      duration: opts.duration ?? (animate ? 0.35 : 0),
      noMoveStart: !animate,
    });
  });
}

/** Unrounded container pixel for a lat/lon (matches trail/marker projection). */
function containerPointForLatLon(lat, lon) {
  const z = map.getZoom();
  const projected = map.project([lat, lon], z);
  const origin = map.getPixelOrigin();
  const pane = map._getMapPanePos();
  return L.point(projected.x - origin.x + pane.x, projected.y - origin.y + pane.y);
}

let followTilesTimer = 0;
let followOverlayRefreshTimer = 0;

/** Load map tiles after raw follow pans without firing moveend (that re-rounds SVG paths). */
function scheduleFollowTiles() {
  if (followTilesTimer) return;
  followTilesTimer = setTimeout(() => {
    followTilesTimer = 0;
    if (!anyFollowActive() || typeof map === "undefined") return;
    map.eachLayer((layer) => {
      if (layer instanceof L.GridLayer && typeof layer._update === "function") {
        try {
          layer._update();
        } catch (_) {
          /* ignore tile update errors mid-pan */
        }
      }
    });
  }, FOLLOW_TILES_MS);
}

function markFollowVectorHeartbeat() {
  followVectorAt = performance.now();
  followVectorPaneMark =
    typeof map !== "undefined" && typeof map._getMapPanePos === "function"
      ? map._getMapPanePos().clone()
      : null;
}

/** Collect Leaflet path renderers used by follow overlays (SVG + canvas). */
function followVectorRenderers() {
  const out = new Set();
  const add = (layer) => {
    if (!layer) return;
    if (typeof layer._update === "function" && layer._container) out.add(layer);
    if (layer._renderer) out.add(layer._renderer);
  };
  add(ownRoute);
  add(selRoute);
  if (typeof trailLive !== "undefined") add(trailLive);
  if (typeof trailCanvas !== "undefined") add(trailCanvas);
  if (typeof trailLiveCanvas !== "undefined") add(trailLiveCanvas);
  if (typeof trail !== "undefined" && trail && typeof trail.eachLayer === "function") {
    trail.eachLayer((layer) => add(layer));
  }
  if (typeof trailLiveGroup !== "undefined" && trailLiveGroup && typeof trailLiveGroup.eachLayer === "function") {
    trailLiveGroup.eachLayer((layer) => add(layer));
  }
  return out;
}

/** True when the visible map is within marginPx of the renderer clip edge. */
function followRendererNearClip(renderer, marginPx = 80) {
  const mapRef = renderer && renderer._map;
  const b = renderer && renderer._bounds;
  if (!mapRef || !b) return true;
  const size = mapRef.getSize();
  if (!size || !size.x) return true;
  const corners = [
    mapRef.containerPointToLayerPoint([-marginPx, -marginPx]),
    mapRef.containerPointToLayerPoint([size.x + marginPx, -marginPx]),
    mapRef.containerPointToLayerPoint([-marginPx, size.y + marginPx]),
    mapRef.containerPointToLayerPoint([size.x + marginPx, size.y + marginPx]),
  ];
  for (let i = 0; i < corners.length; i += 1) {
    if (!b.contains(corners[i])) return true;
  }
  return false;
}

/**
 * Expand renderer viewport only when near clip. SVG: detach → attrs → reattach
 * so position+viewBox don't paint as two frames (the usual flash).
 */
function softUpdateFollowRenderer(renderer) {
  if (!renderer || !renderer._map) return false;
  if (renderer._bounds && !followRendererNearClip(renderer)) return false;

  const el = renderer._container;
  const isSvg = el && el.tagName === "svg";
  if (!isSvg) {
    try {
      if (typeof renderer._update === "function") renderer._update();
    } catch (_) {
      /* ignore */
    }
    return true;
  }

  try {
    L.Renderer.prototype._update.call(renderer);
  } catch (_) {
    try {
      renderer._update();
    } catch (__) {
      /* ignore */
    }
    return true;
  }

  const b = renderer._bounds;
  if (!b) return false;
  const size = b.getSize();
  const parent = el.parentNode;
  const next = el.nextSibling;
  if (parent) parent.removeChild(el);
  try {
    if (!renderer._svgSize || !renderer._svgSize.equals(size)) {
      renderer._svgSize = size;
      el.setAttribute("width", size.x);
      el.setAttribute("height", size.y);
    }
    L.DomUtil.setPosition(el, b.min);
    el.setAttribute("viewBox", [b.min.x, b.min.y, size.x, size.y].join(" "));
  } finally {
    if (parent) {
      if (next) parent.insertBefore(el, next);
      else parent.appendChild(el);
    }
  }
  return true;
}

/**
 * Soft vector heal while raw-panning — no setView, no forceLineRestroke flash.
 * Viewport only when near clip; light nudge keeps tip current.
 */
function refreshFollowVectors() {
  if (!anyFollowActive()) return;
  const ac = typeof followTargetAc === "function" ? followTargetAc() : null;
  if (!ac || !Number.isFinite(ac.lat) || !Number.isFinite(ac.lon)) return;

  for (const renderer of followVectorRenderers()) {
    softUpdateFollowRenderer(renderer);
  }

  const id = ac.isOwnship || ac.id === "ownship" ? "ownship" : ac.id;
  const pose =
    typeof motionPoseAt === "function" ? motionPoseAt(id, performance.now()) : null;
  const p =
    pose && Number.isFinite(pose.lat)
      ? pose
      : { lat: ac.lat, lon: ac.lon, alt: ac.alt };

  const line = ac.isOwnship || ac.id === "ownship" ? ownRoute : selRoute;
  const pts = line && typeof line.getLatLngs === "function" ? line.getLatLngs() : null;
  const pathMissing = !line || !line._path || !pts || pts.length < 2;

  if (typeof nudgeLiveRoute === "function") {
    nudgeLiveRoute(ac, p, pathMissing ? { full: true } : undefined);
  }
  if (typeof nudgeLiveTrail === "function") nudgeLiveTrail(ac, p);

  markFollowVectorHeartbeat();
}

/** Time- or drift-based vector heal while raw-panning in follow. */
function maybeFollowVectorHeartbeat() {
  if (!anyFollowActive()) return;
  const now = performance.now();
  const dueTime = !followVectorAt || now - followVectorAt >= FOLLOW_VECTOR_MS;
  let dueDrift = !followVectorPaneMark;
  if (followVectorPaneMark && typeof map._getMapPanePos === "function") {
    const p = map._getMapPanePos();
    const dx = p.x - followVectorPaneMark.x;
    const dy = p.y - followVectorPaneMark.y;
    dueDrift = dx * dx + dy * dy >= FOLLOW_VECTOR_DRIFT_PX * FOLLOW_VECTOR_DRIFT_PX;
  }
  if (!dueTime && !dueDrift) return;
  refreshFollowVectors();
}

/**
 * After hard/rare setView: restroke live route/trail.
 * full=true also rebuilds coloured history (user pan-away / large catch-up).
 */
function refreshFollowOverlays(opts = {}) {
  const ac = typeof followTargetAc === "function" ? followTargetAc() : null;
  if (!ac) return;
  const full = opts.full === true;
  if (ownRoute) ownRoute._efbOriginKey = "";
  if (selRoute) selRoute._efbOriginKey = "";
  if (typeof trailLive !== "undefined" && trailLive) trailLive._efbOriginKey = "";

  if (typeof syncRoutes === "function") syncRoutes(ac, { full: true });
  const id = ac.isOwnship || ac.id === "ownship" ? "ownship" : ac.id;
  const pose =
    typeof motionPoseAt === "function" ? motionPoseAt(id, performance.now()) : null;
  const p =
    pose && Number.isFinite(pose.lat)
      ? pose
      : { lat: ac.lat, lon: ac.lon, alt: ac.alt };
  if (typeof nudgeLiveRoute === "function") nudgeLiveRoute(ac, p, { full: true });
  if (typeof nudgeLiveTrail === "function") nudgeLiveTrail(ac, p);
  if (typeof forceLineRestroke === "function") {
    forceLineRestroke(ac.isOwnship || ac.id === "ownship" ? ownRoute : selRoute);
    if (typeof trailLive !== "undefined") forceLineRestroke(trailLive);
  }
  if (typeof trailCanvas !== "undefined" && trailCanvas && typeof trailCanvas._update === "function") {
    try {
      trailCanvas._update();
    } catch (_) {
      /* ignore */
    }
  }
  if (
    typeof trailLiveCanvas !== "undefined" &&
    trailLiveCanvas &&
    typeof trailLiveCanvas._update === "function"
  ) {
    try {
      trailLiveCanvas._update();
    } catch (_) {
      /* ignore */
    }
  }

  const now = performance.now();
  const trailDue = now - followTrailFullAt >= FOLLOW_TRAIL_FULL_MS;
  if (!full && !trailDue) return;

  followTrailFullAt = now;
  if (typeof paintedReconKey !== "undefined") paintedReconKey = null;
  if (typeof paintedLiveSig !== "undefined") paintedLiveSig = "";
  const trailMatch =
    trailAcId &&
    (trailAcId === ac.id ||
      ((ac.isOwnship || ac.id === "ownship") && (trailAcId === "ownship" || (lastOwn && trailAcId === lastOwn.id))));
  if (typeof showHistory === "function" && trailMatch) {
    showHistory(ac);
  } else if (typeof armTrail === "function") {
    armTrail(ac);
  }
}

function scheduleFollowOverlayRefresh(opts = {}) {
  if (followOverlayRefreshTimer) clearTimeout(followOverlayRefreshTimer);
  followOverlayRefreshTimer = setTimeout(() => {
    followOverlayRefreshTimer = 0;
    if (!anyFollowActive()) return;
    refreshFollowOverlays(opts);
  }, 0);
}

/**
 * Subpixel follow pan via Leaflet pane transform.
 * Experimental follow stays on _rawPanBy; setView only for user catch-up / rare drift safety.
 */
function panFollowToLatLon(lat, lon) {
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return false;
  if (mapIsUserZooming()) return false;
  const target = mapVisibleCenterContainerPoint();
  if (!target) return false;
  const cur = containerPointForLatLon(lat, lon);
  let dx = cur.x - target.x;
  let dy = cur.y - target.y;
  const dist2 = dx * dx + dy * dy;
  const experimental = typeof isExperimentalRender === "function" && isExperimentalRender();
  const pane =
    typeof map._getMapPanePos === "function" ? map._getMapPanePos() : null;
  const paneDrift2 = pane ? pane.x * pane.x + pane.y * pane.y : 0;
  // No frequent soft commits — only an extreme-drift safety valve.
  const rareCommit = paneDrift2 > FOLLOW_RARE_COMMIT_PX * FOLLOW_RARE_COMMIT_PX;

  if (dist2 < FOLLOW_DEADBAND2 && !followNeedsViewCommit && !rareCommit) {
    if (experimental) dampFollowSpring(0.7);
    return false;
  }

  const hardSnap =
    followNeedsViewCommit ||
    dist2 > FOLLOW_SNAP_PX * FOLLOW_SNAP_PX ||
    typeof map._rawPanBy !== "function" ||
    !pane;

  if (hardSnap || rareCommit) {
    followNeedsViewCommit = false;
    resetFollowSpring();
    const zoom = map.getZoom();
    const center = mapCenterForPointAtVisibleCenter(lat, lon, zoom);
    withFollowMoveLock(() => {
      map.setView(center, zoom, { reset: true });
    });
    scheduleFollowOverlayRefresh({ full: true });
    markFollowVectorHeartbeat();
    return true;
  }

  if (experimental) {
    const now = performance.now();
    const dt = followSpringAt
      ? Math.min(0.05, Math.max(0.001, (now - followSpringAt) / 1000))
      : 1 / 60;
    followSpringAt = now;
    const w = Math.PI * 2 * FOLLOW_SPRING_HZ;
    followSpringVx += (w * w * dx - 2 * w * followSpringVx) * dt;
    followSpringVy += (w * w * dy - 2 * w * followSpringVy) * dt;
    dx = followSpringVx * dt;
    dy = followSpringVy * dt;
    if (dx * dx + dy * dy < 1e-6) return false;
  }

  followMoveLock += 1;
  try {
    map._rawPanBy(L.point(dx, dy));
  } finally {
    followMoveLock = Math.max(0, followMoveLock - 1);
  }
  scheduleFollowTiles();
  maybeFollowVectorHeartbeat();
  return true;
}

/** Departure/Arrival table row: center map with standard zoom. */
function focusFidsAircraft(ac, animate = true) {
  if (!ac || !Number.isFinite(ac.lat) || !Number.isFinite(ac.lon)) return;
  const zoom = fidsZoomForAc(ac);
  const center = mapCenterForPointAtVisibleCenter(ac.lat, ac.lon, zoom);
  map.setView(center, zoom, { animate });
  bumpChrome();
}

function focusOwnshipOnMap(ac, animate = true) {
  if (!ac || !Number.isFinite(ac.lat) || !Number.isFinite(ac.lon)) return;
  const zoom = Math.max(map.getZoom(), 13);
  const center = mapCenterForPointAtVisibleCenter(ac.lat, ac.lon, zoom);
  map.setView(center, zoom, { animate });
}

function watchingOther() {
  return Boolean(selectedId && selectedId !== "ownship");
}

function acIsOwnship(ac) {
  if (!ac) return false;
  return Boolean(ac.isOwnship || ac.id === "ownship" || (lastOwn && ac.id === lastOwn.id));
}

function anyFollowActive() {
  return Boolean(followMe || followAcId);
}

function paintFollowMeButton() {
  const btn = document.getElementById("btn-follow-me");
  if (!btn) return;
  btn.classList.toggle("is-active", followMe);
  btn.setAttribute("aria-pressed", followMe ? "true" : "false");
}

function followAcForPanel() {
  if (typeof liveStatsAc === "function") {
    const live = liveStatsAc();
    if (live) return live;
  }
  if (!selectedId) return null;
  const mk = markers.has(selectedId) ? markers.get(selectedId) : null;
  if (mk && mk.acData) return mk.acData;
  if (lastOwn && (selectedId === lastOwn.id || selectedId === "ownship")) return lastOwn;
  return (lastTraffic || []).find((a) => a.id === selectedId) || null;
}

function paintFollowAcButton() {
  let active = false;
  if (selectedId) {
    const isOwn = selectedId === "ownship" || (lastOwn && selectedId === lastOwn.id);
    active = isOwn ? followMe : followAcId === selectedId;
  }
  for (const id of ["btn-follow-ac", "btn-follow-ac-phone"]) {
    const btn = document.getElementById(id);
    if (!btn) continue;
    btn.classList.toggle("is-active", active);
    btn.setAttribute("aria-pressed", active ? "true" : "false");
  }
}

function selectOwnshipLikeClick() {
  if (!lastOwn) return;
  const prev = selectedId;
  clearFidsPin();
  openPanel(lastOwn);
  if (prev && markers.has(prev)) refreshIcon(markers.get(prev));
  if (markers.has(lastOwn.id)) refreshIcon(markers.get(lastOwn.id));
  showOwnSurface();
}

function clearFollowTraffic() {
  if (!followAcId) return;
  followAcId = null;
  resetFollowSpring();
  paintFollowAcButton();
}

function setFollowMe(on, opts = {}) {
  followMe = Boolean(on);
  localStorage.setItem("efb-follow-me", followMe ? "1" : "0");
  if (followMe) followAcId = null;
  resetFollowSpring();
  paintFollowMeButton();
  paintFollowAcButton();
  if (followMe) {
    followPausedUntil = 0;
    selectOwnshipLikeClick();
    if (lastOwn) {
      const zoom = opts.applyZoom ? followZoomForAc(lastOwn) : map.getZoom();
      const center = mapCenterForPointAtVisibleCenter(lastOwn.lat, lastOwn.lon, zoom);
      withFollowMoveLock(() => {
        // reset:true clears any prior _rawPanBy offset; avoid animate (locks follow).
        map.setView(center, zoom, { reset: true });
      });
      followPausedUntil = 0;
    }
  }
}

/** Follow a traffic aircraft (not ownship). Pass null/false to exit. */
function setFollowTraffic(id, opts = {}) {
  const next = id || null;
  resetFollowSpring();
  if (next) {
    if (followMe) {
      followMe = false;
      localStorage.setItem("efb-follow-me", "0");
      paintFollowMeButton();
    }
    followAcId = next;
    followPausedUntil = 0;
    const ac =
      opts.ac ||
      (markers.has(next) ? markers.get(next).acData : null) ||
      (lastTraffic || []).find((a) => a.id === next);
    if (ac && Number.isFinite(ac.lat) && Number.isFinite(ac.lon)) {
      const zoom = opts.applyZoom ? followZoomForAc(ac) : map.getZoom();
      const center = mapCenterForPointAtVisibleCenter(ac.lat, ac.lon, zoom);
      withFollowMoveLock(() => {
        map.setView(center, zoom, { reset: true });
      });
      followPausedUntil = 0;
    }
  } else {
    followAcId = null;
  }
  paintFollowAcButton();
}

function toggleFollowForAc(ac) {
  if (!ac) return;
  if (acIsOwnship(ac)) {
    const next = !followMe;
    setFollowMe(next, { applyZoom: next });
    return;
  }
  const next = followAcId !== ac.id;
  setFollowTraffic(next ? ac.id : null, { applyZoom: next, ac });
}

/** Drop traffic-follow when selection changes; keep followMe only on ownship. */
function syncFollowWithSelection(ac) {
  if (!ac) {
    clearFollowTraffic();
    paintFollowAcButton();
    return;
  }
  if (acIsOwnship(ac)) {
    if (followAcId) followAcId = null;
    paintFollowAcButton();
    return;
  }
  if (followMe) setFollowMe(false);
  if (followAcId && followAcId !== ac.id) followAcId = null;
  paintFollowAcButton();
}

function markFollowPaused() {
  followPausedUntil = Date.now() + FOLLOW_PAUSE_MS;
  followNeedsViewCommit = true;
}

function pauseFollowFromUser() {
  if (!anyFollowActive() || followMoveLock) return;
  markFollowPaused();
}

function mapIsUserZooming() {
  return Boolean(map._animatingZoom || (map.touchZoom && map.touchZoom._zooming));
}

function followTargetAc() {
  if (followMe && lastOwn) return lastOwn;
  if (!followAcId) return null;
  if (markers.has(followAcId) && markers.get(followAcId).acData) return markers.get(followAcId).acData;
  return (lastTraffic || []).find((a) => a.id === followAcId) || null;
}

function followOwnshipIfNeeded() {
  if (Date.now() < followPausedUntil) return;
  const ac = followTargetAc();
  if (!ac || !Number.isFinite(ac.lat) || !Number.isFinite(ac.lon)) return;
  // Experimental rAF already pans every frame; setView here would snap and jitter lines.
  if (typeof isExperimentalRender === "function" && isExperimentalRender() && anyFollowActive()) {
    return;
  }
  panFollowToLatLon(ac.lat, ac.lon);
}

function followOwnshipSmooth(now) {
  if (!followMe || !lastOwn) return;
  if (Date.now() < followPausedUntil) return;
  if (mapIsUserZooming()) return;
  if (!Number.isFinite(lastOwn.lat) || !Number.isFinite(lastOwn.lon)) return;
  panFollowToLatLon(lastOwn.lat, lastOwn.lon);
}

/** Keep map locked to interpolated pose of a followed traffic aircraft. */
function followTargetSmooth(now, pose) {
  if (!followAcId) return;
  if (Date.now() < followPausedUntil) return;
  if (mapIsUserZooming()) return;
  if (!pose || !Number.isFinite(pose.lat) || !Number.isFinite(pose.lon)) return;
  panFollowToLatLon(pose.lat, pose.lon);
}

function bumpChrome() {
  const chrome = document.getElementById("chrome");
  chrome.classList.remove("hidden");
  clearTimeout(hideTimer);
  hideTimer = setTimeout(() => {
    if (!document.getElementById("options-menu").hidden) return;
    chrome.classList.add("hidden");
  }, 7000);
}

function setDarkMode(on) {
  document.documentElement.classList.toggle("dark", on);
  document.documentElement.style.colorScheme = on ? "dark only" : "light only";
  const meta = document.querySelector('meta[name="color-scheme"]');
  if (meta) meta.setAttribute("content", on ? "dark" : "light");
  localStorage.setItem("efb-dark", on ? "1" : "0");
  document.getElementById("dark-mode").checked = on;
}
