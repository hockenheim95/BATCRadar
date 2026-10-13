let lastTraffic = [];
let lastAirport = null;
/** Boards computed by the server, keyed by airport ICAO (see fids.py). */
let lastFids = {};
/** Arrival ids the server latched as landed (id -> true) and per-board status labels. */
const fidsArrLatch = new Map();
const fidsArrLate = new Map();
const fidsStat = { dep: new Map(), arr: new Map() };
let lastEnrouteWx = null;
let panelMode = "hidden";
let lastFidsAt = 0;
let depList = [];
let arrList = [];
let fidsOwnId = null;
let fidsAp = "";
/** Keep showing this origin/dest board while picking FIDS rows (incl. not map-loaded). */
let fidsPinnedAp = "";
let aptTab = "info";
/** After Dep/Arr tab open, scroll once so now sits mid-viewport. */
let fidsScrollToNow = false;
/** Last aircraft payload for a FIDS row id if the live pool briefly drops it. */
const fidsRowCache = new Map();
const ENR_TRAFFIC_MAX = 5;
let enrTrafficCapacity = ENR_TRAFFIC_MAX;

function atAirport(ac) {
  if (!ac) return false;
  if (ac.onGround) return true;
  const st = (ac.state || "").toLowerCase();
  if (st === "parked" || st === "taxi" || st === "pushback") return true;
  return (ac.gs || 0) < 40 && (ac.alt || 0) < 2500;
}

function esc(text) {
  return String(text || "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[ch]));
}

function fidsPlace(ac, kind) {
  const city = String(ac[`${kind}Name`] || "").trim();
  if (city && city !== "—") return city;
  const iata = (ac[`${kind}Iata`] || "").replace("—", "").toUpperCase();
  const icao = (ac[kind] || "").toUpperCase();
  if (iata && iata.length === 3) return iata;
  return icao || "—";
}

/** Board airport: where this aircraft is, only if that field is the player's origin or dest. */
function fidsAircraftAirport(ac) {
  const standing = operationAirportIcao(ac);
  return fidsPinAllowed(standing) ? standing : "";
}

/** Player physically at own origin/dest (or short post-departure hold / approach to dest). */
function playerAirportIcao(ac) {
  const own = ac || lastOwn;
  if (!own) return "";
  const origin = (own.origin || "").toUpperCase();
  const dest = (own.dest || "").toUpperCase();
  const here = [own.lat, own.lon];
  const oLL = originLL(own);
  const dLL = destLL(own);
  const oKm = oLL ? kmBetween(here, oLL) : Infinity;
  const dKm = dLL ? kmBetween(here, dLL) : Infinity;
  const atOrigin = Number.isFinite(oKm) && oKm < 8;
  const atDest = Number.isFinite(dKm) && dKm < 8;
  if (dest && !atOrigin && (isApproaching(own) || own.atDestination || atDestField(own) || atDest)) {
    return dest;
  }
  if (origin && atOrigin) return origin;
  const left = leftFieldAt.get(own.id);
  if (origin && left && Date.now() - left < DEP_HOLD_MS) return origin;
  return "";
}

/** Operation panel ICAO: ownship only when you are on the field; traffic uses their flight phase. */
function operationAirportIcao(ac) {
  if (!ac) return "";
  const phase = airportPhase(ac);
  const icao = phase.mode === "airport" && phase.icao ? phase.icao.toUpperCase() : "";
  if (!icao) return "";
  const traffic = !!(ac.id && ac.id !== "ownship" && !ac.isOwnship);
  const origin = (traffic ? ac.origin : (lastOwn?.origin || ac.origin) || "").toUpperCase();
  const dest = (traffic ? ac.dest : (lastOwn?.dest || ac.dest) || "").toUpperCase();
  if (icao !== origin && icao !== dest) {
    const board = (fidsPinnedAp || fidsAp || "").toUpperCase();
    if (!(traffic && fidsArrivedOnBoard(ac) && board && icao === board)) return "";
  }
  if (traffic) return icao;
  const playerAp = playerAirportIcao(lastOwn);
  return playerAp === icao ? icao : "";
}

/** Airport banner: keep the FIDS board field while Departures/Arrivals is open. */
function panelAirportIcao(ac) {
  if (aptTab === "dep" || aptTab === "arr") {
    const board = playerFidsIcao();
    if (board) return board;
  }
  return operationAirportIcao(ac);
}

function fidsPinAllowed(ap) {
  const pin = (ap || "").toUpperCase();
  if (!pin || !lastOwn) return false;
  const po = (lastOwn.origin || "").toUpperCase();
  const pd = (lastOwn.dest || "").toUpperCase();
  return pin === po || pin === pd;
}

function pinFidsAirport(ap) {
  const pin = (ap || "").toUpperCase();
  fidsPinnedAp = fidsPinAllowed(pin) ? pin : "";
}

function clearFidsPin() {
  fidsPinnedAp = "";
}

function fidsBoardHasId(ap, id) {
  if (!ap || !id) return false;
  const board = lastFids[ap];
  if (!board) return false;
  const hit = (rows) => Array.isArray(rows) && rows.some((row) => row && row.id === id);
  return hit(board.dep) || hit(board.arr);
}

function fidsAcById(id) {
  if (!id) return null;
  const mk = markers.get(id);
  if (mk && mk.acData) return mk.acData;
  const fromPool = lastTraffic.find((row) => row.id === id);
  if (fromPool) return fromPool;
  const fromDep = depList.find((row) => row.id === id);
  if (fromDep) return fromDep;
  const fromArr = arrList.find((row) => row.id === id);
  if (fromArr) return fromArr;
  if (lastOwn && lastOwn.id === id) return lastOwn;
  return null;
}

/** Player origin/dest board for the selected aircraft's current field. Pin keeps the board if it drops off the map. */
function fidsAirportIcao() {
  const standing = fidsAircraftAirport(liveStatsAc() || lastOwn);
  if (standing) return standing;
  const pin = (fidsPinnedAp || "").toUpperCase();
  return fidsPinAllowed(pin) ? pin : "";
}

function playerFidsIcao() {
  return fidsAirportIcao();
}

/** Map legacy / mixed-case server labels to current arrivals board wording. */
function normalizeFidsArrSt(st, lateMin) {
  if (!Array.isArray(st) || st.length < 2) return ["Scheduled", "sched"];
  const label = String(st[0] || "");
  const cls = st[1] || "sched";
  if (label === "APPROACH" || label === "Approach") {
    if (lateMin != null) return ["DELAYED", "delay"];
    return ["ON TIME", "ontime"];
  }
  if (label === "En route") return ["EN ROUTE", "air"];
  if (label === "Arrived") return ["ARRIVED", "arrived"];
  if (label === "Taxi") return ["TAXI", "taxi"];
  if (label.startsWith("DELAYED ")) return ["DELAYED", "delay"];
  return [label, cls];
}

function fidsStatus(ac, kind) {
  const hit = fidsStat[kind].get(ac.id);
  return hit ? { label: hit[0], cls: hit[1] } : { label: "Scheduled", cls: "sched" };
}

function fidsDepBoardCount(ac) {
  const boards = lastFids || {};
  const origin = (ac && ac.origin ? String(ac.origin) : "").toUpperCase();
  if (origin && Array.isArray(boards[origin]?.dep)) return boards[origin].dep.length;
  if (!ac || !ac.id) return 0;
  for (const board of Object.values(boards)) {
    const rows = board && board.dep;
    if (!Array.isArray(rows)) continue;
    if (rows.some((row) => row && row.id === ac.id)) return rows.length;
  }
  return 0;
}

/** True if this aircraft currently has a row on any server Departures board. */
function fidsDepListed(ac) {
  if (!ac || !ac.id) return false;
  const boards = lastFids || {};
  for (const board of Object.values(boards)) {
    const rows = board && board.dep;
    if (!Array.isArray(rows)) continue;
    for (const row of rows) {
      if (row && row.id === ac.id) return true;
    }
  }
  return false;
}

function fidsArrivedOnBoard(ac) {
  if (!ac || !ac.id) return false;
  if (fidsArrLatch.has(ac.id)) return true;
  const hit = fidsStat.arr.get(ac.id);
  const label = Array.isArray(hit) ? String(hit[0] || "") : "";
  return label === "ARRIVED" || label === "LANDED" || label === "TAXI";
}
