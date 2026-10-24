const TOUCH_UI = (navigator.maxTouchPoints || 0) > 0 || window.matchMedia("(pointer: coarse)").matches;
const OWN_COLOR_DEFAULT = "#e11d48";

function ownColorPref() {
  const raw = (localStorage.getItem("efb-own-color") || OWN_COLOR_DEFAULT).trim();
  return /^#[0-9a-fA-F]{6}$/.test(raw) ? raw.toLowerCase() : OWN_COLOR_DEFAULT;
}

function applyOwnColor(hex) {
  const next = /^#[0-9a-fA-F]{6}$/.test(hex || "") ? hex.toLowerCase() : OWN_COLOR_DEFAULT;
  localStorage.setItem("efb-own-color", next);
  document.documentElement.style.setProperty("--own-ac", next);
  if (typeof markers === "undefined") return next;
  for (const marker of markers.values()) {
    if (!marker.acData?.isOwnship) continue;
    marker.iconKey = "";
    refreshIcon(marker);
  }
  return next;
}

/** viewBox 0..80 silhouette paths (same geometry as former inline SVG). */
const SILHOUETTE_PATHS = {
  prop: {
    path: "M40 8c1.6 0 2.8 1.6 2.8 4.6V22l20 4v5L42.8 27v22l11 8.5v5L40 55.5 26.2 62.5v-5l11-8.5V27L17.2 31v-5l20-4V12.6C37.2 9.6 38.4 8 40 8z",
    ellipses: [
      [22, 26, 7.5, 3.2],
      [58, 26, 7.5, 3.2],
    ],
  },
  bizjet: {
    path: "M40 7c1.7 0 2.9 1.7 2.9 4.8V22l17 8v5L42.9 27v20l9 8v4.8L40 53.2 28.1 59.8V55l9-8V27L20.1 35v-5l17-8V11.8C37.1 8.7 38.3 7 40 7z",
    ellipses: [
      [32, 48, 3.1, 6],
      [48, 48, 3.1, 6],
    ],
  },
  quad: {
    path: "M40 4c2 0 3.3 2 3.3 6.2V18L76 33.5v5.4L43.3 28.2V50l16 12v5.2L40 59.2 20.7 67.4v-5.2l16-12V28.2L4 38.9v-5.4L36.7 18V10.2C36.7 6 38 4 40 4z",
    ellipses: [
      [16, 35, 3.2, 7],
      [26, 33, 3.2, 7],
      [54, 33, 3.2, 7],
      [64, 35, 3.2, 7],
    ],
  },
  twin: {
    path: "M40 4c2 0 3.3 2 3.3 6.2V18L76 33.5v5.4L43.3 28.2V50l16 12v5.2L40 59.2 20.7 67.4v-5.2l16-12V28.2L4 38.9v-5.4L36.7 18V10.2C36.7 6 38 4 40 4z",
    ellipses: [
      [20, 34, 3.4, 7.2],
      [60, 34, 3.4, 7.2],
    ],
  },
};

const iconBitmapCache = new Map();
const ICON_PAD = 4;
const ICON_INK = "#111111";

function shapeOf(ac) {
  const t = `${ac.type || ""} ${ac.typeName || ""}`.toUpperCase();
  if (
    /A38[08]?|747|B74[0-8]|A340|A34[2-6]|IL62|IL76|IL86|IL96|A124|AN124|AN225|A225|AN12|A400|C130|C30J|\bC5(M)?\b|\bC17\b|GLOBEMASTER|GALAXY|B52|B70[137]|B720|\b707\b|DC-?8|B46[123]|RJ70|RJ85|RJ1H|CONC|VC10|K35R/.test(
      t,
    )
  ) {
    return "quad";
  }
  if (
    /GLF[2-6]?|GL5T|GL6T|GL7T|GLEX|GALX|G280|G150|G200|GULFSTREAM|GLOBAL|CL30|CL35|CL60|CL65|\bCL3\b|\bCL6\b|CHALLENGER|C25[ABCM]?|C500|C501|C510|C525|C550|C55B|C560|C56X|C650|C680|C68A|C700|C750|CITATION|LATITUDE|LONGITUDE|E50P|E55P|E35L|E545|E550|PHENOM|PRAETOR|H25[ABC]|HAWKER|HS125|FA7X|FA8X|FA50|FA20|FA10|F900|F2TH|FALCON|LJ[2-7]\d|LEAR|HDJT|HONDAJET|PC24|BE40|PRM1/.test(
      t,
    )
  ) {
    return "bizjet";
  }
  if (
    /A318|A319|A320|A321|A19N|A20N|A21N|A32[NS]|A300|A30B|A306|A310|A330|A33[23789]|A350|A359|A35K|BCS[13]|A220|B71[27]|B73[1-9]|B37M|B38M|B39M|B3XM|737|B727|B75[237]|757|B76[2347]|767|B77[238LW]|777|B78[89X]|787|E17[05]|E75[LS]|E19[05]|E29[05]|E135|E145|E45X|CRJ[1279X]?|MD8[0-9]|MD90|MD11|DC-?9|DC-?10|L101|F70|\bF100\b|F28|SU95|C919|AJ27|ARJ|MC21|T204|T214|TU154|A148|A158|\bP8\b/.test(
      t,
    )
  ) {
    return "twin";
  }
  return "prop";
}

function iconSize(ac, selected) {
  const zoom = map.getZoom();
  const zoomScale = Math.max(0.78, Math.min(1.28, 0.7 + (zoom - 5) * 0.07));
  const shape = shapeOf(ac);
  const typeScale = shape === "quad" ? 1.06 : shape === "prop" ? 0.9 : shape === "bizjet" ? 0.92 : 1;
  const bump = (ac.isOwnship ? 1.08 : 1) * (selected && !ac.isOwnship ? 1.06 : 1);
  return Math.round(26 * typeScale * zoomScale * bump);
}

function noseFrac(shape) {
  if (shape === "prop") return 0.1;
  if (shape === "bizjet") return 0.088;
  return 0.055;
}

function headingOf(ac) {
  const h = Number(ac && ac.hdg);
  return Number.isFinite(h) ? ((h % 360) + 360) % 360 : 0;
}

function displayHdg(ac) {
  const hdg = headingOf(ac);
  const gs = Number(ac && ac.gs) || 0;
  const samples = typeof motionById !== "undefined" ? motionById.get(ac && ac.id)?.samples : null;
  if (gs < 25 || !samples || samples.length < 2) return hdg;
  const a = [samples[samples.length - 2].lat, samples[samples.length - 2].lon];
  const b = [samples[samples.length - 1].lat, samples[samples.length - 1].lon];
  if (kmBetween(a, b) < 0.06) return hdg;
  const track = bearingTo(a, b);
  let d = Math.abs(hdg - track) % 360;
  if (d > 180) d = 360 - d;
  if (d > 55) return track;
  return hdg;
}

function iconKey(ac, selected) {
  const own = ac.isOwnship ? ownColorPref() : "0";
  return `${shapeOf(ac)}|${iconSize(ac, selected)}|${own}|${selected ? 1 : 0}`;
}

function fillFor(ac, selected) {
  if (ac.isOwnship) return ownColorPref();
  return selected ? "#2f6bff" : "#f5c518";
}

function drawSilhouette(ctx, shape, fill) {
  const def = SILHOUETTE_PATHS[shape] || SILHOUETTE_PATHS.twin;
  ctx.fillStyle = fill;
  ctx.strokeStyle = ICON_INK;
  ctx.lineWidth = 2.2;
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  const path = new Path2D(def.path);
  ctx.stroke(path);
  ctx.fill(path);
  for (const [cx, cy, rx, ry] of def.ellipses) {
    ctx.beginPath();
    ctx.ellipse(cx, cy, rx, ry, 0, 0, Math.PI * 2);
    ctx.stroke();
    ctx.fill();
  }
}

function iconBitmapUrl(shape, px, fill) {
  const key = `${shape}|${px}|${fill}`;
  let url = iconBitmapCache.get(key);
  if (url) return url;
  const dpr = Math.min(2, Math.max(1, window.devicePixelRatio || 1));
  const pad = ICON_PAD;
  const side = px + pad * 2;
  const canvas = document.createElement("canvas");
  canvas.width = Math.ceil(side * dpr);
  canvas.height = Math.ceil(side * dpr);
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, side, side);
  ctx.save();
  ctx.shadowColor = "rgba(0,0,0,0.55)";
  ctx.shadowBlur = 1.5;
  ctx.shadowOffsetY = 1;
  ctx.translate(pad, pad);
  ctx.scale(px / 80, px / 80);
  drawSilhouette(ctx, shape, fill);
  ctx.restore();
  url = canvas.toDataURL("image/png");
  iconBitmapCache.set(key, url);
  return url;
}

function iconFor(ac, selected) {
  const fill = fillFor(ac, selected);
  const px = iconSize(ac, selected);
  const shape = shapeOf(ac);
  const hdg = displayHdg(ac);
  const origin = (noseFrac(shape) * 100).toFixed(2);
  const tip = px * noseFrac(shape);
  const src = iconBitmapUrl(shape, px, fill);
  const cls = `ac-wrap${ac.isOwnship ? " you" : ""}${selected ? " sel" : ""}`;
  return L.divIcon({
    className: "ac-icon",
    html: `<img class="${cls}" width="${px}" height="${px}" alt="" draggable="false" src="${src}" style="width:${px}px;height:${px}px;transform-origin:50% ${origin}%;transform:rotate(${hdg}deg)">`,
    iconSize: [px, px],
    iconAnchor: [px / 2, tip],
  });
}

function applyHeading(marker, ac) {
  const hdg = displayHdg(ac);
  if (marker.hdgDrawn === hdg && marker._acWrap && marker._acWrap.isConnected) return;
  let wrap = marker._acWrap;
  if (!wrap || !wrap.isConnected) {
    const el = marker.getElement();
    if (!el) return;
    wrap = el.querySelector(".ac-wrap");
    marker._acWrap = wrap;
  }
  if (!wrap) return;
  const origin = `${(noseFrac(shapeOf(ac)) * 100).toFixed(2)}%`;
  wrap.style.transformOrigin = `50% ${origin}`;
  if (marker.hdgDrawn === hdg) return;
  marker.hdgDrawn = hdg;
  wrap.style.transform = `rotate(${hdg}deg)`;
}

function refreshIcon(marker) {
  const ac = marker.acData;
  if (!ac) return;
  const sel = ac.id === selectedId;
  const key = iconKey(ac, sel);
  if (marker.iconKey === key) {
    applyHeading(marker, ac);
    return;
  }
  marker.iconKey = key;
  marker.hdgDrawn = displayHdg(ac);
  marker._acWrap = null;
  marker._efbPt = null;
  marker.setIcon(iconFor(ac, sel));
  requestAnimationFrame(() => {
    if (!marker.acData) return;
    applyHeading(marker, marker.acData);
    const id = marker.acData.id;
    const pose = typeof motionById !== "undefined" ? motionById.get(id)?.pose : null;
    if (pose && typeof setMarkerLatLngSmooth === "function") {
      setMarkerLatLngSmooth(marker, pose.lat, pose.lon);
    }
  });
}
