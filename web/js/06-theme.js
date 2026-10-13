let hideTimer = null;
const FOLLOW_PAUSE_MS = 10000;
let followMe = localStorage.getItem("efb-follow-me") === "1";
let followPausedUntil = 0;
let followMoveLock = 0;

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

function paintFollowMeButton() {
  const btn = document.getElementById("btn-follow-me");
  if (!btn) return;
  btn.classList.toggle("is-active", followMe);
  btn.setAttribute("aria-pressed", followMe ? "true" : "false");
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

function setFollowMe(on, opts = {}) {
  followMe = Boolean(on);
  localStorage.setItem("efb-follow-me", followMe ? "1" : "0");
  paintFollowMeButton();
  if (followMe) {
    followPausedUntil = 0;
    selectOwnshipLikeClick();
    if (lastOwn) {
      const zoom = opts.applyZoom ? followZoomForAc(lastOwn) : map.getZoom();
      const center = mapCenterForPointAtVisibleCenter(lastOwn.lat, lastOwn.lon, zoom);
      withFollowMoveLock(() => {
        map.setView(center, zoom, { animate: true, duration: 0.25 });
      });
    }
  }
}

function pauseFollowFromUser() {
  if (!followMe || followMoveLock) return;
  followPausedUntil = Date.now() + FOLLOW_PAUSE_MS;
}

function mapIsUserZooming() {
  return Boolean(map._animatingZoom || (map.touchZoom && map.touchZoom._zooming));
}

function followOwnshipIfNeeded() {
  if (!followMe || !lastOwn) return;
  if (Date.now() < followPausedUntil) return;
  panToMapCenter(lastOwn.lat, lastOwn.lon, { animate: false });
}

function followOwnshipSmooth(now) {
  if (!followMe || !lastOwn) return;
  if (Date.now() < followPausedUntil) return;
  if (mapIsUserZooming()) return;
  if (!Number.isFinite(lastOwn.lat) || !Number.isFinite(lastOwn.lon)) return;
  const t = typeof now === "number" ? now : performance.now();
  if (followOwnshipSmooth._leafAt && t - followOwnshipSmooth._leafAt < 280) return;
  followOwnshipSmooth._leafAt = t;
  followOwnshipIfNeeded();
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
