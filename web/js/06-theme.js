let hideTimer = null;
const FOLLOW_PAUSE_MS = 10000;
/** After user pan-away, catch-up larger than this uses setView so canvas trails/routes redraw. */
const FOLLOW_SNAP_PX = 72;
let followMe = localStorage.getItem("efb-follow-me") === "1";
/** Traffic aircraft id when following a non-ownship target; mutually exclusive with followMe. */
let followAcId = null;
let followPausedUntil = 0;
let followMoveLock = 0;
/** True after a user pause — next follow pan must commit a real map view (restore path/route). */
let followNeedsViewCommit = false;

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
const FOLLOW_ZOOM_AIR = 13;

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
  }, 220);
}

/** After a committed setView catch-up: redraw dashed route + altitude path. */
function refreshFollowOverlays() {
  const ac = typeof followTargetAc === "function" ? followTargetAc() : null;
  if (!ac) return;
  if (ownRoute) ownRoute._efbOriginKey = "";
  if (selRoute) selRoute._efbOriginKey = "";
  if (typeof trailLive !== "undefined" && trailLive) trailLive._efbOriginKey = "";

  if (typeof syncRoutes === "function") syncRoutes(ac);
  const id = ac.isOwnship || ac.id === "ownship" ? "ownship" : ac.id;
  const pose =
    typeof motionPoseAt === "function" ? motionPoseAt(id, performance.now()) : null;
  const p =
    pose && Number.isFinite(pose.lat)
      ? pose
      : { lat: ac.lat, lon: ac.lon, alt: ac.alt };
  if (typeof nudgeLiveRoute === "function") nudgeLiveRoute(ac, p);
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
  // Force coloured history path to repaint for the new viewport.
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

function scheduleFollowOverlayRefresh() {
  if (followOverlayRefreshTimer) clearTimeout(followOverlayRefreshTimer);
  // Run twice: once ASAP, once after Leaflet moveend/canvas settle.
  followOverlayRefreshTimer = setTimeout(() => {
    followOverlayRefreshTimer = 0;
    if (!anyFollowActive()) return;
    refreshFollowOverlays();
    setTimeout(() => {
      if (anyFollowActive()) refreshFollowOverlays();
    }, 120);
  }, 0);
}

/**
 * Subpixel follow pan via Leaflet pane transform — no setView snap.
 * Large catch-ups (after user pan-away) use setView so canvas path/route redraw.
 */
function panFollowToLatLon(lat, lon) {
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return false;
  if (mapIsUserZooming()) return false;
  const target = mapVisibleCenterContainerPoint();
  if (!target) return false;
  const cur = containerPointForLatLon(lat, lon);
  const dx = cur.x - target.x;
  const dy = cur.y - target.y;
  const dist2 = dx * dx + dy * dy;
  if (dist2 < 0.0025) return false;

  const snap =
    followNeedsViewCommit ||
    dist2 > FOLLOW_SNAP_PX * FOLLOW_SNAP_PX ||
    typeof map._rawPanBy !== "function" ||
    typeof map._getMapPanePos !== "function";

  if (snap) {
    followNeedsViewCommit = false;
    const zoom = map.getZoom();
    const center = mapCenterForPointAtVisibleCenter(lat, lon, zoom);
    withFollowMoveLock(() => {
      map.setView(center, zoom, { animate: false });
    });
    scheduleFollowOverlayRefresh();
    return true;
  }

  followMoveLock += 1;
  try {
    map._rawPanBy(L.point(dx, dy));
  } finally {
    followMoveLock = Math.max(0, followMoveLock - 1);
  }
  scheduleFollowTiles();
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
  paintFollowAcButton();
}

function setFollowMe(on, opts = {}) {
  followMe = Boolean(on);
  localStorage.setItem("efb-follow-me", followMe ? "1" : "0");
  if (followMe) followAcId = null;
  paintFollowMeButton();
  paintFollowAcButton();
  if (followMe) {
    followPausedUntil = 0;
    selectOwnshipLikeClick();
    if (lastOwn) {
      const zoom = opts.applyZoom ? followZoomForAc(lastOwn) : map.getZoom();
      const center = mapCenterForPointAtVisibleCenter(lastOwn.lat, lastOwn.lon, zoom);
      withFollowMoveLock(() => {
        // No animate: delayed zoomstart would outlive followMoveLock and pause follow.
        map.setView(center, zoom, { animate: false });
      });
      followPausedUntil = 0;
    }
  }
}

/** Follow a traffic aircraft (not ownship). Pass null/false to exit. */
function setFollowTraffic(id, opts = {}) {
  const next = id || null;
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
        map.setView(center, zoom, { animate: false });
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
