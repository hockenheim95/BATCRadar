function setLiveStats(ac) {
  if (!ac) return;
  const hdg = Number(ac.hdg);
  paintAltGs(ac);
  setText("p-hdg", fmtDash(Number.isFinite(hdg), `${Math.round(hdg).toString().padStart(3, "0")}°`));
  const phase = flightPhaseHtml(ac);
  for (const phaseEl of document.querySelectorAll(".live-meta")) {
    phaseEl.innerHTML = phase || "";
    phaseEl.hidden = !phase;
  }
  const freq = String(ac.freq || "").trim();
  const freqName = withAbbrevs(ac.freqName || "", ac);
  const freqEl = document.getElementById("p-freq");
  const nameEl = document.getElementById("p-freq-name");
  const freqRow = document.getElementById("p-freq-row");
  if (freqEl) freqEl.textContent = freq || "—";
  if (nameEl) {
    nameEl.textContent = freqName ? `(${freqName})` : "";
    nameEl.hidden = !freqName;
  }
  if (freqRow) freqRow.hidden = !freq && !freqName;
  setProgress(ac);
  refreshAirportWx(ac);
}

function unitPref() {
  const raw = localStorage.getItem("efb-units") || "us";
  if (raw === "metric" || raw === "us") return raw;
  return "alternate";
}

function unitSwapMetric() {
  return Math.floor(Date.now() / 15000) % 2 === 1;
}

function useMetricUnits() {
  const pref = unitPref();
  if (pref === "metric") return true;
  if (pref === "us") return false;
  return unitSwapMetric();
}

function liveStatsAc() {
  if (panelMode !== "flight") return null;
  const mk = selectedId ? markers.get(selectedId) : null;
  if (mk && mk.acData) return mk.acData;
  if (lastOwn && selectedId === lastOwn.id) return lastOwn;
  if (selectedId) {
    const hit = lastTraffic.find((ac) => ac.id === selectedId);
    if (hit) return hit;
  }
  return null;
}

function groundspeedDisplayOk(ac) {
  if (!ac) return false;
  const st = String(ac.state || "").toLowerCase();
  if (st === "parked") return false;
  const gs = Number(ac.gs);
  if (!Number.isFinite(gs)) return false;
  if (ac.isOwnship && gs <= 0) return false;
  return true;
}

function paintAltGs(ac) {
  if (!ac) return;
  const metric = useMetricUnits();
  const altOk = Number.isFinite(Number(ac.alt));
  const gsOk = groundspeedDisplayOk(ac);
  if (metric) {
    setText("p-alt", fmtDash(altOk, `${Math.round(Number(ac.alt) * 0.3048).toLocaleString("de-DE")} m`));
    setText("p-gs", fmtDash(gsOk, `${Math.round(Number(ac.gs) * 1.852).toLocaleString("de-DE")} km/h`));
  } else {
    setText("p-alt", fmtDash(altOk, `${Math.round(ac.alt).toLocaleString("de-DE")} ft`));
    setText("p-gs", fmtDash(gsOk, `${Math.round(Number(ac.gs) * 1.150779).toLocaleString("de-DE")} mph`));
  }
}

let lastUnitPhase = useMetricUnits();
setInterval(() => {
  if (unitPref() !== "alternate") return;
  const phase = unitSwapMetric();
  if (phase === lastUnitPhase) return;
  lastUnitPhase = phase;
  paintAltGs(liveStatsAc());
  setProgress(liveStatsAc());
  paintAirportWx();
  if (lastEnrouteWx && document.getElementById("apt-box-title")?.textContent === "Flight Information") {
    paintEnrouteWx(lastEnrouteWx);
  }
  if (lastAirport?.tempC != null) {
    setText("apt-temp", fmtTempC(lastAirport.tempC) || "—");
  }
}, 250);

function shownAc(marker) {
  return marker && marker.acData;
}

let lastHistAt = 0;

