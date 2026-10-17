const FLIGHT_SIGN_FILES = {
  enroute: "enroute",
  ground: "ground",
  departing: "departing",
  approaching: "approaching",
};

function flightSignVer() {
  return document.querySelector('meta[name="efb-version"]')?.content || "";
}

function flightSignSvg(phase, { large = false } = {}) {
  const file = FLIGHT_SIGN_FILES[phase] || FLIGHT_SIGN_FILES.enroute;
  const wrap = large
    ? "flight-sign flight-sign--route"
    : "flow-ico flow-ico--tab flow-ico--sign";
  return `<span class="${wrap}" data-phase="${phase}" aria-hidden="true"><img class="flight-sign-img" alt="" width="96" height="96" decoding="async" src="/img/phase/${file}.png?v=${flightSignVer()}"></span>`;
}

/** Altitude trend for route phase sign (ground / departing / enroute / approaching).
 * Climb/descent latch once seen from live alt trend; level flight does not snap back to enroute.
 * Opposite clear vertical rate can still switch climb ↔ descent. Ground clears the latch. */
const motionSign = new Map();
const MOTION_ALT_MIN_MS = 1500;
const MOTION_CLIMB_FPM = 350;
const MOTION_DESC_FPM = -350;

function isGroundPhaseForSign(ac) {
  if (!ac) return true;
  const st = (ac.state || "").toLowerCase();
  if (ac.onGround) return true;
  if (st === "parked" || st === "taxi" || st === "pushback" || st === "player") return true;
  if (ac.atDestination) {
    const gs = Number(ac.gs) || 0;
    if (gs < 60) return true;
  }
  const gs = Number(ac.gs) || 0;
  const alt = Number(ac.alt) || 0;
  if (gs < 45 && alt < 3000 && atAirport(ac)) return true;
  return false;
}

function flightMotionPhase(ac) {
  if (!ac) return "ground";
  if (isGroundPhaseForSign(ac)) {
    if (ac.id) motionSign.delete(ac.id);
    return "ground";
  }
  const alt = Number(ac.alt);
  const now = Date.now();
  if (!Number.isFinite(alt)) {
    return motionSign.get(ac.id)?.phase || "enroute";
  }
  let entry = motionSign.get(ac.id);
  if (!entry) {
    entry = { alt, at: now, rate: 0, phase: "enroute" };
    motionSign.set(ac.id, entry);
    return entry.phase;
  }
  const dt = now - entry.at;
  if (dt >= MOTION_ALT_MIN_MS) {
    const instant = ((alt - entry.alt) / dt) * 60000;
    entry.rate = entry.rate ? entry.rate * 0.55 + instant * 0.45 : instant;
    entry.alt = alt;
    entry.at = now;
  }
  const r = entry.rate;
  let phase = entry.phase;
  if (r > MOTION_CLIMB_FPM) phase = "departing";
  else if (r < MOTION_DESC_FPM) phase = "approaching";
  entry.phase = phase;
  motionSign.set(ac.id, entry);
  return phase;
}

function paintRoutePhaseSign(ac) {
  const el = document.getElementById("route-phase-sign");
  if (!el) return;
  if (!ac) {
    el.innerHTML = "";
    return;
  }
  el.innerHTML = flightSignSvg(flightMotionPhase(ac), { large: true });
}

function tzForEnroute(ac) {
  if (!ac) return "";
  const destTz = tzFor(ac, "dest");
  const origTz = tzFor(ac, "origin");
  const dest = destLL(ac);
  const origin = originLL(ac);
  if (dest && origin && Number.isFinite(ac.lat) && Number.isFinite(ac.lon)) {
    const toDest = kmBetween([ac.lat, ac.lon], dest);
    const toOrig = kmBetween([ac.lat, ac.lon], origin);
    if (Number.isFinite(toDest) && Number.isFinite(toOrig)) {
      return toDest <= toOrig ? destTz || origTz : origTz || destTz;
    }
  }
  return destTz || origTz;
}

function initFlightSignTabs() {
  document.querySelectorAll("[data-flight-sign]").forEach((slot) => {
    const phase = slot.getAttribute("data-flight-sign");
    if (phase) slot.innerHTML = flightSignSvg(phase);
  });
}

