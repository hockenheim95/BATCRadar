function setAptTab(tab) {
  const next = tab === "dep" || tab === "arr" ? tab : "info";
  if (next === "dep" || next === "arr") fidsScrollToNow = true;
  aptTab = next;
  applyAptTab();
  if (aptTab === "dep" || aptTab === "arr") fitFidsTable();
}

function applyAptTab() {
  const tabs = document.getElementById("apt-box-tabs");
  const title = document.getElementById("apt-box-title");
  const details = document.getElementById("apt-box-details");
  const place = document.getElementById("apt-box-place");
  const fids = document.getElementById("apt-fids");
  const enrTraffic = document.getElementById("enr-traffic");
  const panel = document.getElementById("panel");
  const showTabs = Boolean(playerFidsIcao());
  if (!showTabs && aptTab !== "info") aptTab = "info";
  const fidsOn = showTabs && (aptTab === "dep" || aptTab === "arr");
  const enroute = title?.textContent === "Flight Information";
  const enrTrafficOn = enroute && !fidsOn;
  if (tabs) tabs.hidden = !showTabs;
  if (tabs) {
    for (const btn of tabs.querySelectorAll(".apt-tab")) {
      btn.classList.toggle("is-active", btn.dataset.tab === aptTab);
    }
  }
  if (details) details.hidden = fidsOn;
  if (place) place.hidden = !enroute;
  if (fids) fids.hidden = !fidsOn;
  if (enrTraffic) enrTraffic.hidden = !enrTrafficOn;
  if (panel) {
    panel.classList.toggle("has-fids-tab", fidsOn);
    panel.classList.toggle("has-enr-traffic", enrTrafficOn);
  }
  if (enrTrafficOn) fitEnrTrafficTable();
}

let lastPanelIdentityKey = "";

function panelIdentityKey(ac) {
  if (!ac) return "";
  return [
    ac.id || "",
    ac.callsign || "",
    ac.airlineIcao || "",
    ac.airline || "",
    ac.registration || "",
    ac.origin || "",
    ac.dest || "",
    ac.originName || "",
    ac.destName || "",
    ac.type || "",
    ac.typeName || "",
    ac.source || "",
  ].join("|");
}

function paintPanelIdentity(ac) {
  if (!ac) return;
  fillPanelHead(ac);
  document.getElementById("p-flight-body").hidden = false;
  const [oBig] = codePair(ac.originIata, ac.origin);
  const [dBig] = codePair(ac.destIata, ac.dest);
  setText("p-origin", oBig);
  setText("p-dest", dBig);
  setText("p-origin-name", ac.originName || ac.origin || "");
  setText("p-dest-name", ac.destName || ac.dest || "");
  lastPanelIdentityKey = panelIdentityKey(ac);
}

/** Re-bind callsign/airline/airports once when traffic identity enriches (e.g. BATC after SimConnect). */
function refreshSelectedPanelIfNeeded(ac) {
  if (!ac || document.getElementById("panel").hidden || panelMode !== "flight") return;
  if (selectedId !== ac.id && !(ac.isOwnship && selectedId === "ownship")) return;
  const key = panelIdentityKey(ac);
  if (!key || key === lastPanelIdentityKey) return;
  paintPanelIdentity(ac);
}

function fillPanelHead(ac) {
  selectedId = ac.id;
  document.getElementById("panel").hidden = false;
  if (typeof fitPhonePanelHeight === "function") requestAnimationFrame(fitPhonePanelHeight);
  if (typeof fitDeskPanelScale === "function") requestAnimationFrame(fitDeskPanelScale);
  setText("p-callsign", ac.callsign);
  setText("p-type", ac.type);
  setText("p-airline", ac.airline);
  setText("p-reg", ac.registration || "—");
  setText("p-typename", ac.typeName || "");
  setAirlineLogo(ac);
  if (typeof syncFollowWithSelection === "function") syncFollowWithSelection(ac);
}

function paintAirportRwys(apt) {
  const rwys = document.getElementById("apt-box-rwys");
  const depCell = document.getElementById("apt-dep-cell");
  const arrCell = document.getElementById("apt-arr-cell");
  const dep = apt?.depInUse || [];
  const arr = apt?.arrInUse || [];
  const hasDep = dep.length > 0;
  const hasArr = arr.length > 0;
  if (rwys) {
    rwys.hidden = !hasDep && !hasArr;
    rwys.classList.remove("is-single");
  }
  if (depCell) depCell.hidden = !hasDep;
  if (arrCell) arrCell.hidden = !hasArr;
  setText("apt-dep", hasDep ? dep.join(", ") : "");
  setText("apt-arr", hasArr ? arr.join(", ") : "");
}

function atisMarkup(raw, icao) {
  const text = String(raw || "").trim();
  const code = String(icao || "").toUpperCase();
  if (!text) return "";
  let rest = text;
  if (code) {
    const re = new RegExp(`^${code}\\s+(ATIS\\b)?`, "i");
    const m = text.match(re);
    if (m) rest = text.slice(m[0].length).trimStart();
  }
  const body = rest ? `<br>${esc(rest)}` : "";
  return `<b>D-ATIS</b>${code ? ` <b>${esc(code)}</b>` : ""}${body}`;
}

function metarMarkup(raw, icao) {
  const text = String(raw || "").trim();
  if (!text) return "—";
  const code = String(icao || "").toUpperCase();
  const icaoPat = code || "[A-Z]{4}";
  const re = new RegExp(`^(METAR(?:\\s+COR)?)(?:\\s+(${icaoPat}))?\\b`, "i");
  const m = text.match(re);
  if (!m) return esc(text);
  const rest = text.slice(m[0].length).trimStart();
  const icaoBit = m[2] ? ` <b>${esc(m[2].toUpperCase())}</b>` : "";
  const body = rest ? `<br>${esc(rest)}` : "";
  return `<b>${esc(m[1].toUpperCase())}</b>${icaoBit}${body}`;
}

function cityAlreadyInAirport(airport, city) {
  const a = airport.toLowerCase();
  const c = city.toLowerCase();
  if (!c || a.includes(c)) return true;
  const skip = new Set(["am", "an", "auf", "bei", "der", "die", "das", "de", "del", "des", "la", "le", "los", "las", "the", "of", "and", "und", "saint", "st"]);
  const tokens = c.split(/[\s\-/,.]+/).filter((t) => t.length >= 3 && !skip.has(t));
  return tokens.some((t) => a.includes(t));
}

const leftFieldAt = new Map();
const seenOnField = new Set();
const DEP_HOLD_MS = 2 * 60 * 1000;
const aptCache = new Map();
let aptFetchIcao = "";
let aptShownIcao = "";
let aptClockTimer = null;
let aptClockTz = "";

function airportPlaceLabel(apt) {
  const airport = String(apt?.airportName || apt?.name || "").trim();
  const city = String(apt?.city || "").trim();
  let place = airport;
  if (airport && city && !cityAlreadyInAirport(airport, city)) {
    place = `${airport}, ${city}`;
  } else if (!place) {
    place = city;
  }
  return place || "—";
}

function tzForAirportIcao(icao, aptTz) {
  const code = (icao || "").toUpperCase();
  if (!code) return "";
  if (lastOwn) {
    if ((lastOwn.origin || "").toUpperCase() === code) return tzFor(lastOwn, "origin");
    if ((lastOwn.dest || "").toUpperCase() === code) return tzFor(lastOwn, "dest");
  }
  return aptTz || "";
}

function formatAptLocalClock(tz) {
  if (!tz) return "";
  try {
    return new Intl.DateTimeFormat("en-US", {
      timeZone: tz,
      hour: "numeric",
      minute: "2-digit",
      hour12: true,
    }).format(new Date());
  } catch {
    return "";
  }
}

function paintAptLocalClock() {
  const clock = document.getElementById("apt-box-local");
  if (!clock || clock.hidden) return;
  const text = formatAptLocalClock(aptClockTz);
  if (text) clock.textContent = text;
}

function startAptClock(tz) {
  aptClockTz = tz || "";
  const clock = document.getElementById("apt-box-local");
  if (clock) {
    const text = formatAptLocalClock(aptClockTz);
    clock.textContent = text;
    clock.hidden = !text;
  }
  clearInterval(aptClockTimer);
  if (!aptClockTz) return;
  aptClockTimer = setInterval(paintAptLocalClock, 30_000);
}

function setAirportBanner(apt) {
  const banner = document.getElementById("apt-box-banner");
  const title = document.getElementById("apt-box-title");
  if (banner) {
    banner.hidden = false;
    banner.classList.add("is-airport");
  }
  if (title) title.textContent = airportPlaceLabel(apt);
  startAptClock(tzForAirportIcao(apt?.icao, apt?.tz));
}

function setEnrouteBanner(ac) {
  const banner = document.getElementById("apt-box-banner");
  const title = document.getElementById("apt-box-title");
  if (banner) {
    banner.hidden = false;
    banner.classList.remove("is-airport");
  }
  if (title) title.textContent = "Flight Information";
  startAptClock(tzForEnroute(ac));
}

function setAptWxLoading(on) {
  const wxEl = document.getElementById("apt-atis");
  if (!wxEl) return;
  wxEl.classList.toggle("is-loading", !!on);
  if (on) wxEl.innerHTML = '<span class="apt-wx-spin" aria-hidden="true"></span>';
}

function noteFieldLeave(ac) {
  if (!ac || !ac.id) return;
  if (ac.onGround || atAirport(ac)) {
    seenOnField.add(ac.id);
    leftFieldAt.delete(ac.id);
    return;
  }
  if (seenOnField.has(ac.id) && !leftFieldAt.has(ac.id)) {
    leftFieldAt.set(ac.id, Date.now());
  }
}

function isApproaching(ac) {
  if (!ac || ac.onGround) return false;
  if (ac.atDestination) return true;
  const st = (ac.state || "").toLowerCase();
  if (st === "approach") return true;
  if (/going\s+around/i.test(ac.status || "")) return true;
  if (/on approach|cleared to land|landing/i.test(ac.status || "")) return true;
  const dest = destLL(ac);
  if (!dest) return false;
  const km = kmBetween([ac.lat, ac.lon], dest);
  return km < 55 && (ac.alt || 0) < 14000;
}

function airportPhase(ac) {
  if (!ac) return { mode: "enroute", icao: "" };
  const origin = (ac.origin || "").toUpperCase();
  const dest = (ac.dest || "").toUpperCase();
  const start = (ac.trackStartIcao || "").toUpperCase();
  const here = [ac.lat, ac.lon];
  const oLL = originLL(ac);
  const dLL = destLL(ac);
  const oKm = oLL ? kmBetween(here, oLL) : Infinity;
  const dKm = dLL ? kmBetween(here, dLL) : Infinity;
  const atOrigin = Number.isFinite(oKm) && oKm < 8;
  const atDest = Number.isFinite(dKm) && dKm < 8;
  const st = (ac.state || "").toLowerCase();
  if (fidsArrivedOnBoard(ac)) {
    const board = (fidsPinnedAp || fidsAp || dest || "").toUpperCase();
    if (board) return { mode: "airport", icao: board };
  }
  if (ac.atDestination || atDestField(ac) || (atDest && !atOrigin)) {
    return { mode: "airport", icao: dest || origin };
  }
  if (isApproaching(ac) && !atOrigin) {
    return { mode: "airport", icao: dest || origin };
  }
  if (ac.onGround || atAirport(ac) || st === "takeoff" || st === "pushback" || st === "taxi") {
    const field = atOrigin ? origin : atDest ? dest : start || origin || dest;
    return { mode: "airport", icao: field };
  }
  const left = leftFieldAt.get(ac.id);
  if (left && Date.now() - left < DEP_HOLD_MS) {
    return { mode: "airport", icao: start || origin || dest };
  }
  return { mode: "enroute", icao: "" };
}

async function loadAirport(icao) {
  const code = (icao || "").toUpperCase();
  if (code.length !== 4) return null;
  const hit = aptCache.get(code);
  const ttl = hit?.data?.wxPending ? 900 : 20000;
  if (hit && Date.now() - hit.at < ttl) return hit.data;
  aptFetchIcao = code;
  try {
    const res = await fetch(`/api/airport?icao=${encodeURIComponent(code)}`);
    const data = await res.json();
    if (aptFetchIcao !== code) return aptCache.get(code)?.data || data;
    aptCache.set(code, { at: Date.now(), data });
    return data;
  } catch (err) {
    return hit ? hit.data : null;
  }
}

function enrouteCityLabel(city) {
  if (!city || !city.name) return "";
  const country = String(city.country || "").trim();
  return country ? `${city.name}, ${country}` : city.name;
}

function hideEnrouteNote() {
  const note = document.getElementById("enr-wx-note");
  if (!note) return;
  note.hidden = true;
  note.classList.remove("is-loading");
  note.innerHTML = "";
}

function paintPlaceWx(kind, loading) {
  const el = document.getElementById("apt-place-wx");
  if (!el) return;
  if (loading) {
    el.hidden = false;
    if (el.querySelector("svg")) return;
    el.className = "apt-wx-ico";
    return;
  }
  if (!kind) {
    if (el.dataset.wxKind === "") return;
    el.dataset.wxKind = "";
    el.hidden = true;
    el.className = "apt-wx-ico";
    return;
  }
  if (el.dataset.wxKind === kind && el.querySelector("svg")) {
    el.hidden = false;
    return;
  }
  el.dataset.wxKind = kind;
  el.hidden = false;
  el.className = `apt-wx-ico ${wxClass(kind)}`;
  el.innerHTML = wxSvg(kind);
}

function paintEnrouteWx(data) {
  const cities = Array.isArray(data?.cities) ? data.cities : [];
  const here = cities[0];
  setText("apt-place", enrouteCityLabel(here) || "—");
  setText("apt-icao", "");
  const wind = Number.isFinite(Number(data?.windFrom)) && Number.isFinite(Number(data?.windKt))
    ? `${String(Math.round(data.windFrom)).padStart(3, "0")}° / ${Math.round(data.windKt)} kt`
    : "—";
  setText("apt-wind", wind);
  setText("apt-temp", fmtTempC(data?.tempC) || "—");
  paintPlaceWx(wxKind(data?.code));
  hideEnrouteNote();
}

async function loadEnrouteWx(ac) {
  if (!ac || !Number.isFinite(Number(ac.lat)) || !Number.isFinite(Number(ac.lon))) return null;
  const key = `enr:${wxCacheKey([ac.lat, ac.lon])}`;
  const hit = wxMem.get(key);
  if (hit && Date.now() - hit.at < 8 * 60 * 1000) return hit.data;
  const qs = `lat=${encodeURIComponent(ac.lat)}&lon=${encodeURIComponent(ac.lon)}&ground=1`;
  const res = await fetch(`/api/weather?${qs}`);
  if (!res.ok) return hit ? hit.data : null;
  const data = await res.json();
  wxMem.set(key, { at: Date.now(), data });
  return data;
}

function showEnrouteBox(ac) {
  const box = document.getElementById("p-airport-body");
  const title = document.getElementById("apt-box-title");
  const place = document.getElementById("apt-box-place");
  const details = document.getElementById("apt-box-details");
  const rwys = document.getElementById("apt-box-rwys");
  const wxEl = document.getElementById("apt-atis");
  if (box) box.hidden = false;
  setEnrouteBanner(ac);
  if (place) place.hidden = false;
  if (details) details.hidden = false;
  if (rwys) {
    rwys.hidden = true;
    rwys.classList.remove("is-single");
  }
  if (wxEl) {
    wxEl.hidden = true;
    wxEl.classList.remove("is-loading");
    wxEl.innerHTML = "";
  }
  hideEnrouteNote();
  aptShownIcao = "";
  setAptWxLoading(false);
  const qnhCell = document.getElementById("apt-qnh-cell");
  const wxGrid = document.getElementById("apt-box-grid");
  if (qnhCell) qnhCell.hidden = true;
  if (wxGrid) wxGrid.classList.add("is-enroute");
  applyAptTab();
  if (lastEnrouteWx) {
    paintEnrouteWx(lastEnrouteWx);
    return;
  }
  setText("apt-place", "—");
  setText("apt-icao", "");
  setText("apt-wind", "—");
  setText("apt-temp", "—");
  paintPlaceWx("", true);
}

async function syncAirportBox(ac) {
  const box = document.getElementById("p-airport-body");
  if (!box || document.getElementById("panel").hidden) return;
  const opIcao = panelAirportIcao(ac);
  if (!opIcao) {
    showEnrouteBox(ac);
    const data = await loadEnrouteWx(ac);
    const live = liveStatsAc() || ac;
    if (panelAirportIcao(live)) return;
    lastEnrouteWx = data;
    paintEnrouteWx(data);
    return;
  }
  applyAptTab();
  if (aptShownIcao !== opIcao) {
    const cached = aptCache.get(opIcao);
    if (cached?.data) paintAirport(cached.data);
    else setAptWxLoading(true);
  }
  const data = await loadAirport(opIcao);
  if (panelAirportIcao(liveStatsAc() || ac) !== opIcao) return;
  paintAirport(data);
}

function paintAirport(info) {
  lastAirport = info || lastAirport;
  const apt = info || lastAirport;
  const box = document.getElementById("p-airport-body");
  if (!apt || !apt.icao) {
    showEnrouteBox(liveStatsAc());
    return;
  }
  if (box) box.hidden = false;
  const wxHidden = document.getElementById("apt-atis");
  paintAirportRwys(apt);
  paintPlaceWx("");
  hideEnrouteNote();
  const qnhCell = document.getElementById("apt-qnh-cell");
  const wxGrid = document.getElementById("apt-box-grid");
  if (qnhCell) qnhCell.hidden = false;
  if (wxGrid) wxGrid.classList.remove("is-enroute");
  if (wxHidden) wxHidden.hidden = false;
  setAirportBanner(apt);
  const wind = Number.isFinite(Number(apt.windFrom)) && Number.isFinite(Number(apt.windKt))
    ? `${String(Math.round(apt.windFrom)).padStart(3, "0")}° / ${Math.round(apt.windKt)} kt`
    : "—";
  setText("apt-wind", wind);
  setText("apt-qnh", Number.isFinite(Number(apt.qnh)) ? `${Math.round(apt.qnh)} hPa` : "—");
  setText("apt-temp", fmtTempC(apt.tempC) || "—");
  const wxEl = document.getElementById("apt-atis");
  if (wxEl) {
    aptShownIcao = apt.icao || aptShownIcao;
    const atisHtml = atisMarkup(apt.atisText, apt.icao);
    const metarHtml = metarMarkup(apt.metar, apt.icao);
    const hasWx = !!(atisHtml || (apt.metar && metarHtml !== "—"));
    if (apt.wxPending && !hasWx) {
      setAptWxLoading(true);
    } else {
      wxEl.classList.remove("is-loading");
      wxEl.innerHTML = atisHtml || metarHtml;
    }
  }
  applyAptTab();
}

function showAirportBoard(own, traffic) {
  openPanel(own);
  const ap = playerAirportIcao(own);
  if (ap) {
    const cached = aptCache.get(ap);
    if (cached?.data) paintAirport(cached.data);
  }
  renderFids(own, traffic || lastTraffic);
  lastFidsAt = Date.now();
}

function showOwnSurface() {
  if (!lastOwn) {
    closePanel();
    return;
  }
  if (atAirport(lastOwn)) showAirportBoard(lastOwn, lastTraffic);
  else {
    openPanel(lastOwn);
    if (markers.has(lastOwn.id)) refreshIcon(markers.get(lastOwn.id));
  }
  syncRoutes(lastOwn);
}

function openPanel(ac) {
  const prev = selectedId;
  panelMode = "flight";
  if (ac && (ac.isOwnship || (lastOwn && ac.id === lastOwn.id))) clearFidsPin();
  paintPanelIdentity(ac);
  setLiveStats(ac);
  setAirportTimes(ac);
  setProgress(ac);
  syncAirportBox(ac);
  if (lastOwn) {
    const fidsOn = aptTab === "dep" || aptTab === "arr";
    if (fidsOn && document.getElementById("apt-fids") && !document.getElementById("apt-fids").hidden) {
      paintFidsSelection();
    } else {
      renderFids(lastOwn, lastTraffic);
    }
  }
  if (prev && prev !== ac.id && markers.has(prev)) refreshIcon(markers.get(prev));
  if (typeof armTrail === "function") armTrail(ac);
}

function closePanel() {
  const prev = selectedId;
  selectedId = null;
  panelMode = "hidden";
  lastPanelIdentityKey = "";
  clearFidsPin();
  hideAirlineLogo();
  wxStamp = "";
  selRoute.setLatLngs([]);
  document.getElementById("panel").hidden = true;
  document.getElementById("panel").classList.remove("has-fids-tab", "has-enr-traffic");
  document.getElementById("p-flight-body").hidden = false;
  if (prev && markers.has(prev)) refreshIcon(markers.get(prev));
  hideHistory();
  if (typeof clearFollowTraffic === "function") clearFollowTraffic();
  if (typeof paintFollowAcButton === "function") paintFollowAcButton();
  if (lastOwn) {
    syncRoutes(lastOwn);
    if (aptTab === "dep" || aptTab === "arr") renderFidsTable();
  }
}

