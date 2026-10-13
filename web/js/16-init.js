const optionsMenu = document.getElementById("options-menu");
const btnFollowMe = document.getElementById("btn-follow-me");
if (btnFollowMe) {
  paintFollowMeButton();
  btnFollowMe.addEventListener("click", (ev) => {
    ev.stopPropagation();
    const next = !followMe;
    setFollowMe(next, { applyZoom: next });
    bumpChrome();
  });
}
map.on("dragstart", () => {
  pauseFollowFromUser();
  bumpChrome();
});
map.on("zoomstart", () => {
  if (followMe) followPausedUntil = Date.now() + FOLLOW_PAUSE_MS;
});
map.getContainer().addEventListener(
  "touchstart",
  () => {
    if (followMe) followPausedUntil = Date.now() + FOLLOW_PAUSE_MS;
  },
  { passive: true }
);
["gesturestart", "gesturechange"].forEach((name) => {
  document.addEventListener(name, (ev) => ev.preventDefault());
});
document.getElementById("btn-options").addEventListener("click", (ev) => {
  ev.stopPropagation();
  optionsMenu.hidden = !optionsMenu.hidden;
  bumpChrome();
});
document.getElementById("options").addEventListener("click", (ev) => ev.stopPropagation());
document.getElementById("dark-mode").addEventListener("change", (ev) => {
  setDarkMode(ev.target.checked);
  bumpChrome();
});
setDarkMode(localStorage.getItem("efb-dark") === "1");
const mapStyleSelect = document.getElementById("opt-map-style");
if (mapStyleSelect) {
  for (const [id, def] of Object.entries(MAP_STYLES)) {
    const opt = document.createElement("option");
    opt.value = id;
    opt.textContent = def.label;
    mapStyleSelect.appendChild(opt);
  }
  mapStyleSelect.value = mapStylePref();
  mapStyleSelect.addEventListener("change", () => {
    setMapStyle(mapStyleSelect.value);
    bumpChrome();
  });
}
const unitsSelect = document.getElementById("opt-units");
if (unitsSelect) {
  unitsSelect.value = unitPref();
  unitsSelect.addEventListener("change", () => {
    localStorage.setItem("efb-units", unitsSelect.value);
    lastUnitPhase = useMetricUnits();
    paintAltGs(liveStatsAc());
    setProgress(liveStatsAc());
    paintAirportWx();
    if (lastEnrouteWx && document.getElementById("apt-box-title")?.textContent === "Flight Information") {
      paintEnrouteWx(lastEnrouteWx);
    }
    bumpChrome();
  });
}
const renderSelect = document.getElementById("opt-render");
if (renderSelect) {
  renderSelect.value = renderPref();
  renderSelect.addEventListener("change", () => {
    setRenderMode(renderSelect.value);
    bumpChrome();
  });
}

map.on("click", () => {
  optionsMenu.hidden = true;
  bumpChrome();
});
map.on("zoomend", () => {
  bumpChrome();
  scheduleReviveTiles();
  for (const marker of markers.values()) {
    marker.iconKey = "";
    if (marker.acData) refreshIcon(marker);
  }
});

function onFidsRow(ev) {
  const tr = ev.target.closest("tr[data-id]");
  if (!tr) return;
  const id = tr.getAttribute("data-id");
  const fromFids = ev.currentTarget.id === "fids-body";
  const fidsBox = document.getElementById("apt-fids");
  const fidsY = fromFids && fidsBox ? fidsBox.scrollTop : 0;
  if (fromFids) pinFidsAirport(fidsAp);
  const ac = fidsAcById(id);
  if (!ac) return;
  if (!ac.isOwnship && !(lastOwn && ac.id === lastOwn.id)) setFollowMe(false);
  openPanel(ac);
  armTrail(ac);
  if (markers.has(ac.id)) refreshIcon(markers.get(ac.id));
  focusFidsAircraft(ac);
  syncRoutes(ac);
  if (fromFids && fidsBox) fidsBox.scrollTop = fidsY;
  if (typeof snapPhoneCarousel === "function") snapPhoneCarousel();
}

document.getElementById("fids-body").addEventListener("click", onFidsRow);
document.getElementById("enr-traffic-body").addEventListener("click", onFidsRow);
document.getElementById("apt-box-tabs").addEventListener("click", (ev) => {
  const btn = ev.target.closest(".apt-tab");
  if (!btn) return;
  setAptTab(btn.dataset.tab);
  if (lastOwn) renderFids(lastOwn, lastTraffic);
});
window.addEventListener("resize", () => {
  if (typeof viewportPageZoomed === "function" && viewportPageZoomed()) return;
  if (typeof mapIsUserZooming === "function" && mapIsUserZooming()) return;
  followOwnshipIfNeeded();
  const title = document.getElementById("apt-box-title");
  if (title?.textContent === "Flight Information" && measureEnrTrafficCapacity()) {
    renderEnrouteTrafficTable();
  }
});
const enrTrafficEl = document.getElementById("enr-traffic");
if (typeof ResizeObserver !== "undefined" && enrTrafficEl) {
  new ResizeObserver(() => {
    const title = document.getElementById("apt-box-title");
    if (title?.textContent !== "Flight Information") return;
    if (measureEnrTrafficCapacity()) renderEnrouteTrafficTable();
  }).observe(enrTrafficEl);
}
document.getElementById("chrome").addEventListener("pointerdown", bumpChrome);

initFlightSignTabs();
tick();
setInterval(tick, TOUCH_UI ? 500 : 400);
bumpChrome();
