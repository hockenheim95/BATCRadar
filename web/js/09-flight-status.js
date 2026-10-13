function airlineLogoCodes(ac) {
  const icao = (ac.airlineIcao || ac.callsign || "").replace(/[^A-Za-z]/g, "").slice(0, 3).toUpperCase();
  const iata = (ac.airlineIata || "").toUpperCase();
  return { icao, iata };
}

function logoMarkText(icao, iata) {
  const code = (iata || icao || "").replace(/[^A-Z0-9]/g, "");
  if (code.length >= 2) return code.slice(0, 2);
  return code || "?";
}

function hideAirlineLogo() {
  const wrap = document.getElementById("p-logo-wrap");
  const img = document.getElementById("p-logo");
  const mark = document.getElementById("p-logo-mark");
  logoKey = "";
  logoSeq += 1;
  if (img) {
    img.onload = null;
    img.onerror = null;
    img.removeAttribute("src");
    img.hidden = true;
  }
  if (mark) {
    mark.hidden = true;
    mark.textContent = "";
  }
  if (wrap) wrap.hidden = true;
}

function setAirlineLogo(ac) {
  const wrap = document.getElementById("p-logo-wrap");
  const img = document.getElementById("p-logo");
  const mark = document.getElementById("p-logo-mark");
  const { icao, iata } = airlineLogoCodes(ac);
  const key = `${icao}:${iata}`;
  if (key === logoKey) return;
  logoKey = key;
  const seq = ++logoSeq;
  img.onload = null;
  img.onerror = null;
  img.removeAttribute("src");
  img.hidden = true;
  mark.hidden = true;
  mark.textContent = logoMarkText(icao, iata);
  if (!icao && !iata) {
    wrap.hidden = true;
    return;
  }
  wrap.hidden = false;
  const showMark = () => {
    if (seq !== logoSeq) return;
    img.hidden = true;
    img.removeAttribute("src");
    mark.hidden = !mark.textContent;
    if (!mark.textContent) wrap.hidden = true;
  };
  if (!iata) {
    showMark();
    return;
  }
  img.onload = () => {
    if (seq !== logoSeq) return;
    mark.hidden = true;
    img.hidden = false;
  };
  img.onerror = showMark;
  img.alt = ac.airline || icao;
  img.src = `/logo/${encodeURIComponent(iata)}.png?v=${encodeURIComponent(flightSignVer())}`;
}


function codePair(iata, icao) {
  const icaoU = (icao || "").replace("—", "").trim().toUpperCase();
  let iataU = (iata || "").replace("—", "").trim().toUpperCase();
  if (!iataU || iataU === icaoU || iataU.length !== 3) iataU = "";
  if (iataU) return [iataU, icaoU ? `(${icaoU})` : ""];
  return [icaoU || "—", ""];
}

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

function fmtDash(ok, text) {
  return ok ? text : "—";
}

function rwyIdent(code) {
  return String(code || "")
    .replace(/^(RWY|RUNWAY)\s*/i, "")
    .trim()
    .toUpperCase();
}

function taxiRunway(ac) {
  const st = String(ac.state || "").toLowerCase();
  const dep = rwyIdent(ac.depRunway);
  const arr = rwyIdent(ac.arrRunway);
  if (st === "taxi" || st === "pushback" || st === "takeoff") {
    if (ac.atDestination) return "";
    return dep;
  }
  if (/cleared to land|on approach|landing/i.test(ac.status || "")) return arr;
  return "";
}

function rwyHtml(ident) {
  if (!ident) return "";
  return `RWY ${esc(ident)}`;
}

const ATC_ABBREVS = ["RWY", "SID", "STAR", "ILS", "VOR", "DME", "ATC", "GND", "TWR", "APP", "DEP", "ATIS", "UNICOM"];

function withAbbrevs(text, ac) {
  let out = String(text || "").toLowerCase();
  const restore = [
    ...ATC_ABBREVS,
    rwyIdent(ac.depRunway),
    rwyIdent(ac.arrRunway),
    ac.origin,
    ac.dest,
    ac.originIata,
    ac.destIata,
  ].filter(Boolean);
  for (const token of restore.sort((a, b) => String(b).length - String(a).length)) {
    const rx = new RegExp(`\\b${String(token).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`, "gi");
    out = out.replace(rx, String(token).toUpperCase());
  }
  out = out.replace(/\b(\d{1,2}[lcr])\b/gi, (_, m) => m.toUpperCase());
  out = out.replace(/\b([a-z]{2,4}\d+[a-z]?)\b/gi, (m) => m.toUpperCase());
  return out;
}

function statusAddsInfo(status, phasePlain, ac) {
  let leftover = String(status || "").toLowerCase();
  const drop = [
    phasePlain,
    "taking off",
    "take off",
    "takeoff",
    "taxiing",
    "taxi to",
    "taxi",
    "pushback",
    "push back",
    "on ground",
    "parked",
    "en route",
    "enroute",
    "cleared to land",
    "on approach",
    "landing",
    "runway",
    "rwy",
    rwyIdent(ac.depRunway),
    rwyIdent(ac.arrRunway),
    ac.origin,
    ac.dest,
    ac.originIata,
    ac.destIata,
  ].filter(Boolean).map((part) => String(part).toLowerCase());
  leftover = leftover.replace(/[^a-z0-9]+/g, " ");
  for (const token of drop.sort((a, b) => b.length - a.length)) {
    const rx = new RegExp(token.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\s+/g, "\\s+"), "gi");
    leftover = leftover.replace(rx, " ");
  }
  leftover = leftover.replace(/\b(to|from|the|at|on|off)\b/g, " ").replace(/\s+/g, " ").trim();
  return leftover.length > 1;
}

function statusOverridesPhase(status) {
  return /\bholding\s+short\b|\bgoing\s+around\b|\blin(?:e|ing)\s+up\b|\bvacating\b/i.test(status || "");
}

function formatOverrideStatus(raw, ac) {
  let text = withAbbrevs(raw || "", ac);
  text = text.replace(/\blining up\b/gi, "line up");
  text = text.replace(/\b[a-z]{4}\s+(\d{1,2}[lcr]?)\b/gi, (_, ident) => `RWY ${ident.toUpperCase()}`);
  const dep = rwyIdent(ac.depRunway);
  const arr = rwyIdent(ac.arrRunway);
  if (arr && /\bvacating\b/i.test(text) && !/\brwy\b/i.test(text)) {
    text = text.replace(/\bvacating\b/i, `vacating RWY ${arr}`);
  }
  const ident = /\bvacating\b/i.test(text) ? arr || dep : dep || arr;
  if (ident && /\bholding short\b/i.test(text) && !/\brwy\b/i.test(text)) {
    text = text.replace(/\bholding short\b/i, `holding short RWY ${ident}`);
  }
  if (ident && /\bline up\b/i.test(text) && !/\brwy\b/i.test(text)) {
    text = text.replace(/\bline up\b/i, `line up RWY ${ident}`);
  }
  return text;
}

const _GATE_NOISE = /PARKING|GATE|STAND|DOCK/gi;

function compactGateLabel(raw) {
  let id = String(raw || "")
    .replace(/^(GATE|STAND|PARKING)\s*/i, "")
    .replace(_GATE_NOISE, "")
    .replace(/\s+/g, "")
    .trim();
  return id;
}

function gateIdent(ac) {
  return compactGateLabel(ac.gate);
}

/** FIDS gate column: short labels only; empty cell when unusable. */
function fidsGateLabel(ac) {
  const id = gateIdent(ac);
  if (!id) return "";
  const digits = id.replace(/\D/g, "");
  if (digits.length > 4) return "";
  if (!digits && id.length > 4) return "";
  if (id.length > 6) return "";
  return id;
}

function destCity(ac) {
  const name = String(ac.destName || "").replace("—", "").trim();
  if (name && !/^[A-Z0-9]{3,4}$/.test(name)) return name;
  const iata = String(ac.destIata || "").replace("—", "").trim().toUpperCase();
  const icao = String(ac.dest || "").replace("—", "").trim().toUpperCase();
  if (iata && iata.length === 3 && iata !== icao) return iata;
  return icao;
}

function flightPhaseHtml(ac) {
  const st = String(ac.state || "").toLowerCase();
  const rwy = taxiRunway(ac);
  const rawStatus = ac.status || "";
  if (rawStatus && statusOverridesPhase(rawStatus)) return esc(formatOverrideStatus(rawStatus, ac));
  const status = withAbbrevs(rawStatus, ac);
  const gate = gateIdent(ac);

  let phase = "";
  if (st === "parked") phase = gate ? `parked gate ${esc(gate)}` : "parked";
  else if (st === "taxi") {
    if (ac.atDestination) phase = "taxi to gate";
    else phase = rwy ? `taxi to ${rwyHtml(rwy)}` : "taxi";
  }
  else if (st === "pushback") phase = rwy ? `pushback · taxi to ${rwyHtml(rwy)}` : "pushback";
  else if (st === "takeoff") phase = rwy ? `takeoff ${rwyHtml(rwy)}` : "takeoff";
  else if (
    st === "flight" ||
    (st === "player" && !ac.onGround) ||
    (ac.isOwnship && !ac.onGround && !st)
  ) {
    const dest = destCity(ac);
    phase = ac.onGround ? "on ground" : dest ? `en route to ${esc(dest)}` : "en route";
  }
  else if (ac.atDestination && ac.onGround) phase = "arrived";
  else if (ac.onGround) phase = "on ground";
  else if (st && st !== "player") phase = withAbbrevs(st, ac);

  const enrouteLike =
    st === "flight" || (st === "player" && !ac.onGround) || (ac.isOwnship && !ac.onGround && !st);
  if (rwy && /approach|land/i.test(status) && enrouteLike) {
    phase = `approach ${rwyHtml(rwy)}`;
  }

  if (!status) return phase;
  const phasePlain = phase.replace(/<[^>]+>/g, "");
  if (!statusAddsInfo(status, phasePlain, ac)) return phase || esc(status);
  if (phase) return `${phase} · ${esc(status)}`;
  return esc(status);
}

