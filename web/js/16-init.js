const optionsMenu = document.getElementById("options-menu");
const btnFollowMe = document.getElementById("btn-follow-me");
if (btnFollowMe) {
  paintFollowMeButton();
  btnFollowMe.addEventListener("click", (ev) => {
    ev.stopPropagation();
    const next = !followMe;
    // Keep current zoom; only re-center on ownship.
    setFollowMe(next);
    bumpChrome();
  });
}
let followAcTapAt = 0;
function onFollowAcButton(ev) {
  ev.preventDefault();
  ev.stopPropagation();
  const now = Date.now();
  if (now - followAcTapAt < 350) return;
  followAcTapAt = now;
  const ac = typeof followAcForPanel === "function" ? followAcForPanel() : null;
  if (!ac) return;
  toggleFollowForAc(ac);
  bumpChrome();
}
for (const id of ["btn-follow-ac", "btn-follow-ac-phone"]) {
  const btn = document.getElementById(id);
  if (!btn) continue;
  btn.addEventListener("click", onFollowAcButton);
}
paintFollowAcButton();
map.on("dragstart", () => {
  pauseFollowFromUser();
  bumpChrome();
});
map.on("dragend", () => {
  // Even if dragstart raced with followMoveLock, catch-up must commit a real setView
  // so dashed route + trail canvas rebuild for the new viewport.
  if (anyFollowActive()) followNeedsViewCommit = true;
});
map.on("zoomstart", () => {
  // Ignore programmatic follow/engage zooms (followMoveLock); only user zoom pauses.
  if (anyFollowActive() && !followMoveLock) markFollowPaused();
});
map.getContainer().addEventListener(
  "touchstart",
  () => {
    if (anyFollowActive() && !followMoveLock) markFollowPaused();
  },
  { passive: true }
);
["gesturestart", "gesturechange"].forEach((name) => {
  document.addEventListener(name, (ev) => ev.preventDefault());
});
document.getElementById("btn-options").addEventListener("click", (ev) => {
  ev.stopPropagation();
  optionsMenu.hidden = !optionsMenu.hidden;
  if (!optionsMenu.hidden) refreshMsfsAutostart();
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

const ownColorInput = document.getElementById("opt-own-color");
if (ownColorInput) {
  ownColorInput.value = applyOwnColor(ownColorPref());
  ownColorInput.addEventListener("input", () => {
    applyOwnColor(ownColorInput.value);
    bumpChrome();
  });
}

const msfsAutostartRow = document.getElementById("opt-msfs-autostart-row");
const msfsAutostartInput = document.getElementById("opt-msfs-autostart");
let msfsAutostartBusy = false;

function applyMsfsAutostartStatus(data) {
  if (!msfsAutostartInput) return;
  const available = !!data?.available;
  const enabled = !!data?.enabled;
  msfsAutostartInput.checked = enabled;
  msfsAutostartInput.disabled = !available || msfsAutostartBusy;
  if (msfsAutostartRow) {
    const tip = available
      ? (data?.exe ? `Writes MSFS exe.xml → ${data.exe}` : "Uses MSFS exe.xml")
      : data?.error || "Not available";
    msfsAutostartRow.title = tip;
    msfsAutostartRow.classList.toggle("is-disabled", !available);
  }
}

function refreshMsfsAutostart() {
  if (!msfsAutostartInput) return;
  fetch("/api/msfs-autostart", { cache: "no-store" })
    .then((res) => (res.ok ? res.json() : null))
    .then((data) => {
      if (!data) return;
      applyMsfsAutostartStatus(data);
    })
    .catch(() => {});
}

if (msfsAutostartInput) {
  refreshMsfsAutostart();
  msfsAutostartInput.addEventListener("change", async () => {
    if (msfsAutostartBusy) return;
    const want = !!msfsAutostartInput.checked;
    msfsAutostartBusy = true;
    msfsAutostartInput.disabled = true;
    try {
      const res = await fetch("/api/msfs-autostart", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: want }),
      });
      const data = await res.json().catch(() => ({}));
      applyMsfsAutostartStatus(data);
      if (!res.ok || !data.ok) {
        alert(data.error || "Could not update MSFS autostart.");
      }
    } catch {
      alert("Could not update MSFS autostart.");
    } finally {
      msfsAutostartBusy = false;
      refreshMsfsAutostart();
    }
  });
}

const portInput = document.getElementById("opt-port");
const portApply = document.getElementById("opt-port-apply");
let livePort = Number(location.port) || (location.protocol === "https:" ? 443 : 80);

function syncPortApply() {
  if (!portInput || !portApply) return;
  const next = Number(portInput.value);
  const dirty = Number.isFinite(next) && next !== livePort;
  portApply.hidden = !dirty;
}

if (portInput) {
  portInput.value = String(livePort);
  fetch("/api/settings", { cache: "no-store" })
    .then((res) => (res.ok ? res.json() : null))
    .then((data) => {
      const port = Number(data?.port);
      if (!Number.isFinite(port)) return;
      livePort = port;
      portInput.value = String(port);
      syncPortApply();
    })
    .catch(() => {});
  portInput.addEventListener("input", syncPortApply);
  portInput.addEventListener("change", syncPortApply);
}
if (portApply) {
  portApply.addEventListener("click", async () => {
    const next = Number(portInput?.value);
    if (!Number.isFinite(next) || next < 1024 || next > 65535) {
      alert("Port must be between 1024 and 65535.");
      return;
    }
    if (next === livePort) {
      syncPortApply();
      return;
    }
    portApply.disabled = true;
    portApply.textContent = "Restarting…";
    try {
      const res = await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ port: next }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) {
        alert(data.error || "Could not change port.");
        portApply.disabled = false;
        portApply.textContent = "Apply & restart";
        return;
      }
      if (!data.restarting) {
        livePort = next;
        portApply.disabled = false;
        portApply.textContent = "Apply & restart";
        syncPortApply();
        return;
      }
      const target = `${location.protocol}//${location.hostname}:${next}/`;
      setTimeout(() => {
        location.href = target;
      }, 900);
    } catch {
      alert("Could not change port.");
      portApply.disabled = false;
      portApply.textContent = "Apply & restart";
    }
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
  if (!ac.isOwnship && !(lastOwn && ac.id === lastOwn.id)) {
    setFollowMe(false);
    if (typeof clearFollowTraffic === "function" && followAcId && followAcId !== ac.id) {
      clearFollowTraffic();
    }
  }
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
