const PHONE_LAYOUT_MQ = window.matchMedia("(max-width: 767px) and (orientation: portrait)");
let phonePanelFitKey = "";

function snapPhoneCarousel() {
  const carousel = document.getElementById("panel-carousel");
  if (!carousel || !PHONE_LAYOUT_MQ.matches) return;
  const slideW = carousel.clientWidth;
  if (slideW <= 0) return;
  const idx = Math.round(carousel.scrollLeft / slideW);
  const x = idx * slideW;
  if (Math.abs(carousel.scrollLeft - x) > 1) carousel.scrollLeft = x;
}

function phonePanelViewportKey() {
  return `${window.innerWidth}x${window.innerHeight}`;
}

function measurePhonePanelNeed() {
  const root = document.documentElement;
  const sheet = document.querySelector(".mobile-sheet--flight");
  const dots = document.getElementById("panel-dots");
  const panel = document.getElementById("panel");
  if (!sheet || !panel) return 0;
  const dotsH = dots ? dots.offsetHeight : 0;
  if (dotsH < 8) return 0;
  const maxH = Math.round(window.innerHeight * 0.72);
  const prev = root.style.getPropertyValue("--phone-panel-h");
  root.style.setProperty("--phone-panel-h", maxH + "px");
  void panel.offsetHeight;
  let content = 0;
  for (const child of sheet.children) {
    content += child.offsetHeight;
  }
  if (prev) root.style.setProperty("--phone-panel-h", prev);
  else root.style.removeProperty("--phone-panel-h");
  return Math.ceil(content + dotsH + 2);
}

function phonePanelFreqOverlap() {
  const freq = document.getElementById("p-freq-row");
  const dots = document.getElementById("panel-dots");
  if (!freq || freq.hidden || !dots) return 0;
  return Math.max(0, Math.ceil(freq.getBoundingClientRect().bottom - dots.getBoundingClientRect().top + 2));
}

function fitPhonePanelHeight() {
  const root = document.documentElement;
  if (!PHONE_LAYOUT_MQ.matches) {
    root.style.removeProperty("--phone-panel-h");
    phonePanelFitKey = "";
    return;
  }
  const panel = document.getElementById("panel");
  if (!panel || panel.hidden) return;

  const viewKey = phonePanelViewportKey();
  if (phonePanelFitKey && phonePanelFitKey !== viewKey) {
    root.style.removeProperty("--phone-panel-h");
    phonePanelFitKey = "";
  }
  const cur = parseFloat(root.style.getPropertyValue("--phone-panel-h")) || 0;
  const overlap = phonePanelFreqOverlap();
  if (phonePanelFitKey === viewKey && cur >= 160 && overlap <= 0) return;

  let needed = cur + overlap;
  if (!(phonePanelFitKey === viewKey && cur >= 160)) {
    needed = Math.max(needed, measurePhonePanelNeed());
  }
  if (needed < 160) return;
  const maxH = Math.round(window.innerHeight * 0.72);
  const h = Math.min(needed, maxH);
  if (cur >= h) {
    phonePanelFitKey = viewKey;
    return;
  }
  root.style.setProperty("--phone-panel-h", h + "px");
  phonePanelFitKey = viewKey;
}

function viewBoxSize() {
  const vv = window.visualViewport;
  if (vv && vv.width > 0 && vv.height > 0) return { w: vv.width, h: vv.height };
  return { w: window.innerWidth, h: window.innerHeight };
}

const DESK_PANEL_DESIGN_W = 340;
const DESK_PANEL_DESIGN_H = 800;
let deskPanelFitKey = "";
let deskPanelFitScale = "1";

function fitDeskPanelScale() {
  const panel = document.getElementById("panel");
  if (!panel) return;
  if (PHONE_LAYOUT_MQ.matches) {
    panel.style.removeProperty("--panel-fit-scale");
    panel.style.removeProperty("height");
    deskPanelFitKey = "";
    return;
  }
  const { w, h } = viewBoxSize();
  const key = `${Math.round(w)}x${Math.round(h)}`;
  if (key !== deskPanelFitKey) {
    const s = Math.max(0.55, Math.min(1, h / DESK_PANEL_DESIGN_H, w / DESK_PANEL_DESIGN_W));
    deskPanelFitKey = key;
    deskPanelFitScale = s.toFixed(3);
  }
  const s = Number(deskPanelFitScale) || 1;
  panel.style.setProperty("--panel-fit-scale", deskPanelFitScale);
  panel.style.height = `${Math.round(h / s)}px`;
}

let lastMapBoxKey = "";

function mapBoxKey() {
  const el = document.getElementById("map");
  if (!el) return "";
  return `${el.clientWidth}x${el.clientHeight}`;
}

function viewportPageZoomed() {
  return Boolean(window.visualViewport && Math.abs(window.visualViewport.scale - 1) > 0.02);
}

function refreshMapSizeIfNeeded() {
  if (typeof map === "undefined" || !map) return;
  if (viewportPageZoomed()) return;
  if (typeof mapIsUserZooming === "function" && mapIsUserZooming()) return;
  const key = mapBoxKey();
  if (!key || key === lastMapBoxKey) return;
  lastMapBoxKey = key;
  map.invalidateSize({ animate: false });
}

function syncPhoneLayout(opts = {}) {
  const phone = PHONE_LAYOUT_MQ.matches;
  document.documentElement.classList.toggle("layout-phone", phone);
  if (!phone) {
    document.documentElement.style.removeProperty("--phone-panel-h");
  }
  refreshMapSizeIfNeeded();
  if (opts.recenter && lastOwn && typeof panToMapCenter === "function") {
    panToMapCenter(lastOwn.lat, lastOwn.lon, { animate: false });
  }
  if (typeof fitFidsTable === "function") fitFidsTable();
  requestAnimationFrame(() => {
    fitPhonePanelHeight();
    fitDeskPanelScale();
  });
}

function paintPanelDots() {
  const carousel = document.getElementById("panel-carousel");
  const dots = document.getElementById("panel-dots");
  if (!carousel || !dots || !PHONE_LAYOUT_MQ.matches) return;
  const slideW = carousel.clientWidth;
  if (slideW <= 0) return;
  const idx = Math.round(carousel.scrollLeft / slideW);
  const children = dots.querySelectorAll(".panel-dot");
  for (let i = 0; i < children.length; i++) {
    children[i].classList.toggle("is-active", i === idx);
  }
}

function bindPanelCarousel() {
  const carousel = document.getElementById("panel-carousel");
  if (!carousel) return;
  let scrollTimer = null;
  carousel.addEventListener(
    "scroll",
    () => {
      clearTimeout(scrollTimer);
      scrollTimer = setTimeout(paintPanelDots, 40);
    },
    { passive: true }
  );
}

syncPhoneLayout();
bindPanelCarousel();
PHONE_LAYOUT_MQ.addEventListener("change", () => syncPhoneLayout({ recenter: true }));
window.addEventListener("resize", () => {
  syncPhoneLayout();
  paintPanelDots();
});
if (window.visualViewport) {
  window.visualViewport.addEventListener("resize", () => {
    refreshMapSizeIfNeeded();
    fitDeskPanelScale();
  });
}
