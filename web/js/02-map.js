const MAP_MAX_ZOOM = 18;
const EMPTY_TILE = (L.Util && L.Util.emptyImageUrl) || "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7";

const BASEMAP_TILE_OPTS = {
  keepBuffer: 2,
  updateWhenIdle: TOUCH_UI,
  updateWhenZooming: false,
  className: "basemap",
  detectRetina: false,
};

function tileTemplate(tmpl, coords) {
  return L.Util.template(tmpl, { s: "a", z: coords.z, x: coords.x, y: coords.y });
}

function attachTileLayer(layer, def) {
  const fallback = (!TOUCH_UI && def && def.fallback) || "";
  const origCreate = layer.createTile;
  const origError = layer._tileOnError;

  layer.createTile = function (coords, done) {
    const tile = origCreate.call(this, coords, done);
    tile._efbCoords = coords;
    tile._efbAt = Date.now();
    return tile;
  };

  layer._tileOnError = function (done, tile, e) {
    if (!tile || tile._efbDead) {
      origError.call(this, done, tile, e);
      return;
    }
    const coords = tile._efbCoords;
    if (coords && !tile._efbRetry) {
      tile._efbRetry = 1;
      tile._efbAt = Date.now();
      tile.src = this.getTileUrl(coords);
      return;
    }
    if (fallback && coords && !tile._efbFallback) {
      tile._efbFallback = 1;
      tile._efbAt = Date.now();
      tile.src = tileTemplate(fallback, coords);
      return;
    }
    origError.call(this, done, tile, e);
  };

  layer.on("tileunload", (ev) => {
    const img = ev.tile;
    if (!img) return;
    img._efbDead = true;
    img.onload = null;
    img.onerror = null;
    img.src = EMPTY_TILE;
  });
}

function reviveTiles() {
  const layer = activeBasemap;
  if (!layer || !layer._tiles) return;
  const now = Date.now();
  for (const t of Object.values(layer._tiles)) {
    if (!t || !t.current || !t.el || !t.coords) continue;
    const img = t.el;
    if (img._efbDead) continue;
    if (img.complete && img.naturalWidth > 0) continue;
    const hung = !img.complete && img._efbAt && now - img._efbAt > 6000;
    const failed = img.complete && img.naturalWidth === 0;
    if (!hung && !failed) continue;
    if ((img._efbRevive || 0) >= 4) continue;
    img._efbRevive = (img._efbRevive || 0) + 1;
    img._efbRetry = 0;
    img._efbDead = false;
    img._efbAt = now;
    img.src = layer.getTileUrl(t.coords);
  }
}

let reviveTimer = 0;
function scheduleReviveTiles() {
  clearTimeout(reviveTimer);
  reviveTimer = setTimeout(reviveTiles, 300);
}

const MAP_STYLES = {
  osm: {
    label: "OpenStreetMap",
    url: "/tiles/{z}/{x}/{y}.png",
    fallback: "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    attribution: "&copy; OpenStreetMap",
    maxZoom: 18,
  },
  esriSat: {
    label: "Esri Satellite",
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    attribution: "Tiles &copy; Esri",
    maxZoom: 18,
  },
  openTopo: {
    label: "OpenTopoMap",
    url: "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
    attribution: "Map data &copy; OpenTopoMap",
    maxZoom: 17,
  },
  osmHot: {
    label: "OSM Humanitarian",
    url: "https://{s}.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png",
    attribution: "&copy; OpenStreetMap, HOT",
    maxZoom: 19,
  },
  esriTopo: {
    label: "Esri Topographic",
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
    attribution: "Tiles &copy; Esri",
    maxZoom: 18,
  },
  esriLightGray: {
    label: "Esri Light Gray",
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
    attribution: "Tiles &copy; Esri",
    maxZoom: 18,
  },
  esriDarkGray: {
    label: "Esri Dark Gray",
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}",
    attribution: "Tiles &copy; Esri",
    maxZoom: 18,
  },
  esriStreet: {
    label: "Esri World Street",
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
    attribution: "Tiles &copy; Esri",
    maxZoom: 18,
  },
  topPlusDe: {
    label: "TopPlusOpen (DE)",
    url: "https://sgx.geodatenzentrum.de/wmts_topplus_open/tile/1.0.0/web/default/WEBMERCATOR/{z}/{y}/{x}.png",
    attribution: '&copy; <a href="https://gdz.bkg.bund.de/">BKG</a>',
    maxZoom: 18,
  },
};

const basemapLayers = new Map();
let activeBasemap = null;

function mapStylePref() {
  const id = localStorage.getItem("efb-map-style") || "osm";
  return MAP_STYLES[id] ? id : "osm";
}

function basemapLayer(id) {
  let layer = basemapLayers.get(id);
  if (layer) return layer;
  const def = MAP_STYLES[id];
  const nativeMax = def.maxNativeZoom ?? def.maxZoom ?? MAP_MAX_ZOOM;
  layer = L.tileLayer(def.url, {
    ...BASEMAP_TILE_OPTS,
    attribution: def.attribution,
    maxNativeZoom: nativeMax,
    maxZoom: Math.max(nativeMax, MAP_MAX_ZOOM),
    subdomains: def.subdomains || "abc",
  });
  attachTileLayer(layer, def);
  basemapLayers.set(id, layer);
  return layer;
}

function setMapStyle(id) {
  const styleId = MAP_STYLES[id] ? id : "osm";
  const next = basemapLayer(styleId);
  if (activeBasemap === next) return;
  if (activeBasemap) map.removeLayer(activeBasemap);
  activeBasemap = next;
  activeBasemap.addTo(map);
  activeBasemap.bringToBack();
  localStorage.setItem("efb-map-style", styleId);
  const sel = document.getElementById("opt-map-style");
  if (sel && sel.value !== styleId) sel.value = styleId;
}

const map = L.map("map", {
  zoomControl: false,
  attributionControl: true,
  touchZoom: true,
  scrollWheelZoom: true,
  fadeAnimation: false,
  zoomAnimation: false,
  markerZoomAnimation: false,
}).setView([49.45, 11.08], 7);

setMapStyle(mapStylePref());
map.on("moveend", scheduleReviveTiles);
map.on("zoomend", scheduleReviveTiles);
requestAnimationFrame(() => {
  map.invalidateSize({ animate: false });
});

let selectedId = null;
let trailAcId = null;
let lastOwn = null;
const markers = new Map();
const localFieldAlt = new Map();
let trail = L.featureGroup().addTo(map);
const trailLiveGroup = L.featureGroup().addTo(map);
const trailCanvas = L.canvas({ padding: 0.6 }).addTo(map);
const trailLiveCanvas = L.canvas({ padding: 0.6 }).addTo(map);
const TRAIL_WEIGHT = 3.2;
const trailLive = L.polyline([], {
  color: "#ffd620",
  weight: TRAIL_WEIGHT,
  opacity: 0.92,
  lineJoin: "round",
  lineCap: "round",
  interactive: false,
}).addTo(map);
const ownRoute = L.polyline([], {
  color: "#111",
  weight: 2.4,
  opacity: 0.9,
  dashArray: "10 8",
}).addTo(map);
const selRoute = L.polyline([], {
  color: "#111",
  weight: 2.4,
  opacity: 0.9,
  dashArray: "10 8",
}).addTo(map);

