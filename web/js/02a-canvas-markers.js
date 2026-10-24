/**
 * =============================================================================
 * EFB_CANVAS_MARKERS — optional aircraft drawing backend (removable)
 * =============================================================================
 *
 * WHAT: Draw all traffic aircraft on ONE Leaflet canvas overlay instead of
 *       per-plane DOM L.marker icons. Same silhouettes/colors via iconBitmapUrl.
 * WHY:  Low-RAM tablets (e.g. Galaxy Tab A SM-T515) choke on ~200 DOM markers.
 * WHEN: User picks Options → Markers → "Canvas (experimental)" ;
 *       localStorage key "efb-markers" = "dom" | "canvas" (default "dom").
 *
 * Works with BOTH render modes (Standard / Experimental): those only change
 * how lat/lon poses are computed; this file only changes how they are painted.
 *
 * TO REMOVE THIS FEATURE COMPLETELY:
 *   1. Delete this file (02a-canvas-markers.js).
 *   2. Remove its <script> tag from index.html.
 *   3. Remove the Options row #opt-markers from index.html.
 *   4. Remove the opt-markers wiring in 16-init.js (search EFB_CANVAS_MARKERS).
 *   5. In 15-tick.js / 15a-motion.js / 01-icons.js remove call sites marked
 *      EFB_CANVAS_MARKERS (DOM-only path remains).
 *   6. Grep the repo for EFB_CANVAS_MARKERS and efb-markers — should be empty.
 *
 * Integration surface (keep stable while the feature exists):
 *   isCanvasMarkers() / markerBackendPref() / setMarkerBackend(mode)
 *   upsertAircraftVisual(ac) / removeAircraftVisual(id) / clearAircraftVisuals()
 *   scheduleCanvasMarkersRedraw()
 *   selectAircraftFromMap(ac)  — shared click selection (DOM + canvas)
 * =============================================================================
 */

const MARKER_BACKEND_DOM = "dom";
const MARKER_BACKEND_CANVAS = "canvas";
const MARKER_BACKEND_KEY = "efb-markers";

function markerBackendPref() {
  const raw = localStorage.getItem(MARKER_BACKEND_KEY);
  return raw === MARKER_BACKEND_CANVAS ? MARKER_BACKEND_CANVAS : MARKER_BACKEND_DOM;
}

let markerBackend = markerBackendPref();

function isCanvasMarkers() {
  return markerBackend === MARKER_BACKEND_CANVAS;
}

/** True for lightweight canvas stubs stored in `markers` (not L.Marker). */
function isCanvasMarkerStub(marker) {
  return Boolean(marker && marker._efbCanvas);
}

// --- canvas layer state -------------------------------------------------------

let canvasAcLayer = null;
let canvasRedrawRaf = 0;
const canvasIconImgCache = new Map();

function canvasIconImage(url) {
  let img = canvasIconImgCache.get(url);
  if (img) return img;
  img = new Image();
  img.decoding = "async";
  img.src = url;
  img.onload = () => scheduleCanvasMarkersRedraw();
  canvasIconImgCache.set(url, img);
  return img;
}

const CanvasAircraftLayer = L.Layer.extend({
  onAdd(map) {
    this._map = map;
    this._canvas = L.DomUtil.create("canvas", "efb-ac-canvas leaflet-zoom-animated");
    this._canvas.style.pointerEvents = "auto";
    this._canvas.style.position = "absolute";
    this._canvas.style.left = "0";
    this._canvas.style.top = "0";
    const pane = map.getPane("markerPane") || map.getPanes().overlayPane;
    pane.appendChild(this._canvas);
    map.on("move", this._onMove, this);
    map.on("moveend zoomend viewreset resize", this._onView, this);
    L.DomEvent.on(this._canvas, "click", this._onClick, this);
    this._reset();
  },

  onRemove(map) {
    map.off("move", this._onMove, this);
    map.off("moveend zoomend viewreset resize", this._onView, this);
    L.DomEvent.off(this._canvas, "click", this._onClick, this);
    if (this._canvas && this._canvas.parentNode) this._canvas.parentNode.removeChild(this._canvas);
    this._canvas = null;
    this._map = null;
  },

  _onMove() {
    this._updatePos();
    this._paint();
  },

  _onView() {
    this._reset();
  },

  _updatePos() {
    if (!this._map || !this._canvas) return;
    const topLeft = this._map.containerPointToLayerPoint([0, 0]);
    L.DomUtil.setPosition(this._canvas, topLeft);
  },

  _reset() {
    if (!this._map || !this._canvas) return;
    const size = this._map.getSize();
    const dpr = Math.min(2, Math.max(1, window.devicePixelRatio || 1));
    this._dpr = dpr;
    this._canvas.width = Math.max(1, Math.floor(size.x * dpr));
    this._canvas.height = Math.max(1, Math.floor(size.y * dpr));
    this._canvas.style.width = `${size.x}px`;
    this._canvas.style.height = `${size.y}px`;
    this._updatePos();
    this._paint();
  },

  _paint() {
    const map = this._map;
    const canvas = this._canvas;
    if (!map || !canvas || typeof markers === "undefined") return;
    const ctx = canvas.getContext("2d");
    const dpr = this._dpr || 1;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const w = canvas.width / dpr;
    const h = canvas.height / dpr;
    ctx.clearRect(0, 0, w, h);

    // Draw traffic first, ownship last (on top) — mirrors DOM zIndexOffset.
    const traffic = [];
    let own = null;
    for (const marker of markers.values()) {
      if (!isCanvasMarkerStub(marker) || !marker.acData) continue;
      if (marker.acData.isOwnship || marker.acData.id === "ownship") own = marker;
      else traffic.push(marker);
    }
    for (const marker of traffic) this._drawOne(ctx, marker);
    if (own) this._drawOne(ctx, own);
  },

  _drawOne(ctx, marker) {
    const ac = marker.acData;
    const ll = marker._latlng;
    if (!ac || !ll || !Number.isFinite(ll.lat) || !Number.isFinite(ll.lng)) return;
    const selected = ac.id === selectedId;
    const px = typeof iconSize === "function" ? iconSize(ac, selected) : 26;
    const shape = typeof shapeOf === "function" ? shapeOf(ac) : "twin";
    const fill =
      typeof fillFor === "function"
        ? fillFor(ac, selected)
        : ac.isOwnship
          ? "#e11d48"
          : selected
            ? "#2f6bff"
            : "#f5c518";
    const url = typeof iconBitmapUrl === "function" ? iconBitmapUrl(shape, px, fill) : "";
    if (!url) return;
    const img = canvasIconImage(url);
    if (!img.complete || !img.naturalWidth) return;

    const pt = this._map.latLngToContainerPoint(ll);
    const tipFrac = typeof noseFrac === "function" ? noseFrac(shape) : 0.055;
    const hdg = Number.isFinite(marker.hdgDrawn)
      ? marker.hdgDrawn
      : typeof displayHdg === "function"
        ? displayHdg(ac)
        : 0;

    // Icon anchor matches DOM DivIcon: center-x, noseFrac from top.
    ctx.save();
    ctx.translate(pt.x, pt.y);
    ctx.rotate((hdg * Math.PI) / 180);
    ctx.drawImage(img, -px / 2, -px * tipFrac, px, px);
    ctx.restore();
  },

  _onClick(ev) {
    const map = this._map;
    if (!map) return;
    L.DomEvent.stop(ev);
    const rect = this._canvas.getBoundingClientRect();
    const x = ev.clientX - rect.left;
    const y = ev.clientY - rect.top;
    const hit = this._hitTest(x, y);
    if (hit && typeof selectAircraftFromMap === "function") selectAircraftFromMap(hit);
  },

  _hitTest(x, y) {
    // Reverse draw order: ownship first in search list.
    const list = [];
    for (const marker of markers.values()) {
      if (!isCanvasMarkerStub(marker) || !marker.acData || !marker._latlng) continue;
      list.push(marker);
    }
    list.sort((a, b) => {
      const ao = a.acData.isOwnship || a.acData.id === "ownship" ? 1 : 0;
      const bo = b.acData.isOwnship || b.acData.id === "ownship" ? 1 : 0;
      return bo - ao;
    });
    for (const marker of list) {
      const ac = marker.acData;
      const selected = ac.id === selectedId;
      const px = typeof iconSize === "function" ? iconSize(ac, selected) : 26;
      const shape = typeof shapeOf === "function" ? shapeOf(ac) : "twin";
      const tipFrac = typeof noseFrac === "function" ? noseFrac(shape) : 0.055;
      const pt = this._map.latLngToContainerPoint(marker._latlng);
      const hdg = Number.isFinite(marker.hdgDrawn)
        ? marker.hdgDrawn
        : typeof displayHdg === "function"
          ? displayHdg(ac)
          : 0;
      // Approximate hit in icon-local space (rotate click around tip/center).
      const dx = x - pt.x;
      const dy = y - pt.y;
      const rad = (-hdg * Math.PI) / 180;
      const lx = dx * Math.cos(rad) - dy * Math.sin(rad);
      const ly = dx * Math.sin(rad) + dy * Math.cos(rad);
      if (lx >= -px / 2 && lx <= px / 2 && ly >= -px * tipFrac && ly <= px * (1 - tipFrac)) {
        return ac;
      }
    }
    return null;
  },
});

function ensureCanvasAcLayer() {
  if (canvasAcLayer || typeof map === "undefined" || !map) return;
  canvasAcLayer = new CanvasAircraftLayer();
  canvasAcLayer.addTo(map);
}

function destroyCanvasAcLayer() {
  if (canvasRedrawRaf) {
    cancelAnimationFrame(canvasRedrawRaf);
    canvasRedrawRaf = 0;
  }
  if (canvasAcLayer && typeof map !== "undefined" && map) {
    map.removeLayer(canvasAcLayer);
  }
  canvasAcLayer = null;
}

function scheduleCanvasMarkersRedraw() {
  if (!isCanvasMarkers()) return;
  if (canvasRedrawRaf) return;
  canvasRedrawRaf = requestAnimationFrame(() => {
    canvasRedrawRaf = 0;
    if (canvasAcLayer && canvasAcLayer._paint) canvasAcLayer._paint();
  });
}

function makeCanvasMarkerStub(ac) {
  const selected = ac.id === selectedId;
  return {
    _efbCanvas: true,
    acData: ac,
    iconKey: typeof iconKey === "function" ? iconKey(ac, selected) : "",
    hdgDrawn: typeof displayHdg === "function" ? displayHdg(ac) : 0,
    _latlng: L.latLng(ac.lat, ac.lon),
    getElement() {
      return null;
    },
    setIcon() {
      /* canvas redraw handles visuals */
    },
  };
}

/** Insert or refresh one aircraft in the active visual backend. */
function upsertAircraftVisual(ac) {
  if (!ac || !ac.id) return;
  if (isCanvasMarkers()) {
    ensureCanvasAcLayer();
    let stub = markers.get(ac.id);
    if (!stub || !isCanvasMarkerStub(stub)) {
      if (stub && !isCanvasMarkerStub(stub) && typeof map !== "undefined") {
        try {
          map.removeLayer(stub);
        } catch (_) {
          /* ignore */
        }
      }
      stub = makeCanvasMarkerStub(ac);
      markers.set(ac.id, stub);
    } else {
      stub.acData = ac;
      const selected = ac.id === selectedId;
      const key = typeof iconKey === "function" ? iconKey(ac, selected) : "";
      if (stub.iconKey !== key) {
        stub.iconKey = key;
        stub.hdgDrawn = typeof displayHdg === "function" ? displayHdg(ac) : stub.hdgDrawn;
      }
      if (Number.isFinite(ac.lat) && Number.isFinite(ac.lon)) {
        stub._latlng.lat = ac.lat;
        stub._latlng.lng = ac.lon;
      }
    }
    scheduleCanvasMarkersRedraw();
    return;
  }
  // DOM path is owned by 15-tick.js (unchanged create/update logic).
}

function removeAircraftVisual(id) {
  const marker = markers.get(id);
  if (!marker) return;
  if (isCanvasMarkerStub(marker)) {
    markers.delete(id);
    scheduleCanvasMarkersRedraw();
    return;
  }
  if (typeof map !== "undefined" && map) {
    try {
      map.removeLayer(marker);
    } catch (_) {
      /* ignore */
    }
  }
  markers.delete(id);
}

function clearAircraftVisuals() {
  for (const id of [...markers.keys()]) removeAircraftVisual(id);
  if (!isCanvasMarkers()) destroyCanvasAcLayer();
}

/**
 * Shared map-click selection used by DOM marker handlers and canvas hit-test.
 * Keep behavior identical to the former inline marker.on("click") in 15-tick.js.
 */
function selectAircraftFromMap(ac) {
  if (!ac) return;
  const current = ac;
  if (current.isOwnship || current.id === "ownship") {
    if (typeof clearFidsPin === "function") clearFidsPin();
    if (typeof openPanel === "function") openPanel(current);
    if (typeof armTrail === "function") armTrail(current);
    if (typeof syncRoutes === "function") syncRoutes(current);
    if (typeof bumpChrome === "function") bumpChrome();
    scheduleCanvasMarkersRedraw();
    return;
  }
  if (typeof setFollowMe === "function") setFollowMe(false);
  if (typeof followAcId !== "undefined" && followAcId && followAcId !== current.id) {
    if (typeof clearFollowTraffic === "function") clearFollowTraffic();
  }
  const prev = selectedId;
  if (typeof clearFidsPin === "function") clearFidsPin();
  if (typeof openPanel === "function") openPanel(current);
  if (typeof armTrail === "function") armTrail(current);
  if (prev && markers.has(prev) && typeof refreshIcon === "function") refreshIcon(markers.get(prev));
  const mk = markers.get(current.id);
  if (mk && typeof refreshIcon === "function") refreshIcon(mk);
  if (typeof ownRoute !== "undefined" && ownRoute) ownRoute.setLatLngs([]);
  if (typeof syncRoutes === "function") {
    const shown = typeof shownAc === "function" && mk ? shownAc(mk) : null;
    syncRoutes(shown || current);
  }
  if (typeof bumpChrome === "function") bumpChrome();
  scheduleCanvasMarkersRedraw();
}

/**
 * Switch Markers setting. Rebuilds visuals from lastTraffic / lastOwn.
 * @param {"dom"|"canvas"} mode
 */
function setMarkerBackend(mode) {
  const next = mode === MARKER_BACKEND_CANVAS ? MARKER_BACKEND_CANVAS : MARKER_BACKEND_DOM;
  if (next === markerBackend) return;
  clearAircraftVisuals();
  destroyCanvasAcLayer();
  markerBackend = next;
  localStorage.setItem(MARKER_BACKEND_KEY, next);
  if (next === MARKER_BACKEND_CANVAS) ensureCanvasAcLayer();

  const fleet = [];
  if (typeof lastOwn !== "undefined" && lastOwn) fleet.push(lastOwn);
  if (typeof lastTraffic !== "undefined" && Array.isArray(lastTraffic)) {
    for (const ac of lastTraffic) {
      if (!ac || !ac.id) continue;
      if (lastOwn && ac.id === lastOwn.id) continue;
      fleet.push(ac);
    }
  }
  for (const ac of fleet) {
    if (typeof mapVisible === "function" && !mapVisible(ac)) continue;
    if (typeof noteMotion === "function") noteMotion(ac);
    if (next === MARKER_BACKEND_CANVAS) {
      upsertAircraftVisual(ac);
    } else if (typeof recreateDomMarker === "function") {
      recreateDomMarker(ac);
    }
  }
  if (typeof ensurePaintMotion === "function") ensurePaintMotion();
  scheduleCanvasMarkersRedraw();
}

// Boot: if preference is canvas, attach layer once map exists (script loads after 02-map.js).
if (isCanvasMarkers() && typeof map !== "undefined" && map) {
  ensureCanvasAcLayer();
}
