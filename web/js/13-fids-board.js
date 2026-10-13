function fidsLogo(ac) {
  const { icao, iata } = airlineLogoCodes(ac);
  const mark = esc(logoMarkText(icao, iata));
  const markEl = `<span class="fids-logo-mark">${mark}</span>`;
  if (!iata) return markEl;
  const src = `/logo/${encodeURIComponent(iata)}.png?v=${encodeURIComponent(flightSignVer())}`;
  return `<img class="fids-logo" alt="" src="${esc(src)}" onerror="this.nextElementSibling.hidden=false;this.remove()"><span class="fids-logo-mark" hidden>${mark}</span>`;
}

/** Rows that fit below the table header inside the side panel (not the table box height). */
function measureTableRowCapacity(box, maxCap) {
  const body = box?.querySelector("tbody");
  if (!box || box.hidden || !body) return 0;
  const panel = document.getElementById("panel");
  if (!panel || panel.hidden) return 0;
  const padB = parseFloat(getComputedStyle(panel).paddingBottom) || 0;
  let panelBottom = panel.getBoundingClientRect().bottom - padB;
  if (typeof isPhoneLayout === "function" && isPhoneLayout()) {
    const dots = document.getElementById("panel-dots");
    if (dots) {
      const dotsTop = dots.getBoundingClientRect().top;
      if (dotsTop > 0) panelBottom = Math.min(panelBottom, dotsTop);
    }
  }
  const thead = box.querySelector("thead");
  const rowsTop = thead
    ? thead.getBoundingClientRect().bottom
    : box.getBoundingClientRect().top;
  const avail = Math.max(0, panelBottom - rowsTop);
  let rowH = 0;
  const tr = body.querySelector("tr");
  if (tr) rowH = tr.getBoundingClientRect().height;
  if (rowH < 8) rowH = 22;
  const cap = maxCap != null ? maxCap : Infinity;
  return Math.min(cap, Math.max(1, Math.floor(avail / rowH)));
}

function enrTrafficFreqKey(ac) {
  return String(ac?.freq || "").trim();
}

function pickTrafficOnFrequency(ref, pool) {
  const freq = enrTrafficFreqKey(ref);
  if (!ref || !freq || !Number.isFinite(Number(ref.lat)) || !Number.isFinite(Number(ref.lon))) return [];
  const here = [ref.lat, ref.lon];
  return (pool || [])
    .filter((ac) => ac && ac.id !== ref.id && enrTrafficFreqKey(ac) === freq)
    .map((ac) => ({ ac, km: kmBetween(here, [ac.lat, ac.lon]) }))
    .sort((a, b) => a.km - b.km);
}

function enrTrafficRow(row) {
  const ac = row.ac;
  const dist = fmtNearDist(row.km) || "—";
  return `<tr data-id="${esc(ac.id)}">
    <td>${fidsLogo(ac)}</td>
    <td class="fids-flt">${esc(ac.flightIata || ac.callsign)}</td>
    <td class="fids-city">${esc(fidsPlace(ac, "origin"))}</td>
    <td class="fids-city">${esc(fidsPlace(ac, "dest"))}</td>
    <td>${esc(dist)}</td>
  </tr>`;
}

function paintEnrTrafficLabel(ref) {
  const label = document.querySelector("#enr-traffic .apt-enr-traffic-label span");
  if (!label) return;
  const freqName = withAbbrevs(ref?.freqName || "", ref);
  label.textContent = freqName
    ? `Traffic on Frequency (${freqName})`
    : "Traffic on Frequency";
}

function renderEnrouteTrafficTable() {
  const body = document.getElementById("enr-traffic-body");
  if (!body) return;
  const ref = liveStatsAc() || lastOwn;
  paintEnrTrafficLabel(ref);
  const list = pickTrafficOnFrequency(ref, lastTraffic).slice(0, enrTrafficCapacity);
  body.innerHTML = list.map((row) => enrTrafficRow(row)).join("") ||
    `<tr><td colspan="5">No traffic on frequency</td></tr>`;
}

function measureEnrTrafficCapacity() {
  const box = document.getElementById("enr-traffic");
  const next = measureTableRowCapacity(box, ENR_TRAFFIC_MAX);
  if (!next || next === enrTrafficCapacity) return false;
  enrTrafficCapacity = next;
  return true;
}

function fitEnrTrafficTable() {
  const title = document.getElementById("apt-box-title");
  if (title?.textContent !== "Flight Information") return;
  renderEnrouteTrafficTable();
  requestAnimationFrame(() => {
    if (!measureEnrTrafficCapacity()) return;
    renderEnrouteTrafficTable();
  });
}

function renderEnrouteTraffic() {
  const title = document.getElementById("apt-box-title");
  const box = document.getElementById("enr-traffic");
  if (!box || title?.textContent !== "Flight Information") {
    if (box) box.hidden = true;
    return;
  }
  fitEnrTrafficTable();
}

function fidsIsOwn(ac, ownId) {
  return Boolean(ac && ownId && (ac.id === ownId || ac.isOwnship));
}

function fidsBatcTimeField(ac, kind) {
  if (!ac) return "";
  const key = kind === "dep" ? "timeFrom" : "timeTo";
  const raw = String(ac[key] || "").trim();
  return parseBatcZForAc(ac, raw) ? raw : "";
}

function fidsSchedMs(ac, kind) {
  const raw = fidsBatcTimeField(ac, kind) || (kind === "dep" ? ac.timeFrom : ac.timeTo);
  return parseBatcZForAc(ac, String(raw || "").trim())?.getTime() ?? null;
}

function fidsRowIndexForNow(list, kind) {
  if (!list.length) return 0;
  const now = Date.now();
  let best = 0;
  let bestDist = Infinity;
  for (let i = 0; i < list.length; i++) {
    const t = fidsSchedMs(list[i], kind);
    const dist = t != null ? Math.abs(t - now) : Infinity;
    if (dist < bestDist) {
      bestDist = dist;
      best = i;
    }
  }
  return best;
}

function scrollFidsToNow(fidsBox, list, kind) {
  if (!fidsBox || fidsBox.hidden || !list.length) return;
  const body = document.getElementById("fids-body");
  if (!body) return;
  const rows = body.querySelectorAll("tr[data-id]");
  const idx = fidsRowIndexForNow(list, kind);
  const row = rows[idx];
  if (!row) return;
  const cRect = fidsBox.getBoundingClientRect();
  const eRect = row.getBoundingClientRect();
  const mid = eRect.top + eRect.height / 2 - (cRect.top + fidsBox.clientHeight / 2);
  fidsBox.scrollTop = Math.max(0, Math.min(fidsBox.scrollTop + mid, fidsBox.scrollHeight - fidsBox.clientHeight));
}

/** Merge /api/traffic ownship with the fleet copy (BATC STD/STA often only on one). */
function fidsOwnAc(own, pool) {
  if (!own) return null;
  const hit = (pool || []).find((ac) => ac.isOwnship || ac.id === own.id);
  if (!hit) return own;
  const timeFrom = fidsBatcTimeField(own, "dep") || fidsBatcTimeField(hit, "dep") || own.timeFrom || hit.timeFrom;
  const timeTo = fidsBatcTimeField(own, "arr") || fidsBatcTimeField(hit, "arr") || own.timeTo || hit.timeTo;
  return { ...hit, ...own, timeFrom, timeTo };
}

function fidsRow(ac, kind, ownId) {
  const st = fidsStatus(ac, kind);
  const time = formatSched(
    kind === "dep" ? ac.timeFrom : ac.timeTo,
    tzFor(ac, kind === "dep" ? "origin" : "dest"),
    ac
  );
  const lateMin = kind === "arr" ? fidsArrLate.get(ac.id) : null;
  const timeCell =
    time.time && lateMin != null
      ? `${esc(time.time)}<span class="fids-late"> +${lateMin}</span>`
      : esc(time.time || "—");
  const place = fidsPlace(ac, kind === "dep" ? "dest" : "origin");
  let rowCls = "";
  if (fidsIsOwn(ac, ownId)) rowCls = ' class="is-own"';
  else if (ac.id === selectedId) rowCls = ' class="is-sel"';
  return `<tr data-id="${esc(ac.id)}"${rowCls}>
    <td>${fidsLogo(ac)}</td>
    <td class="fids-flt">${esc(ac.flightIata || ac.callsign)}</td>
    <td class="fids-city">${esc(place)}</td>
    <td class="fids-time">${timeCell}</td>
    <td class="fids-gate">${esc(fidsGateLabel(ac))}</td>
    <td class="fids-st ${st.cls}">${esc(st.label)}</td>
  </tr>`;
}

function hydrateFidsRow(row, kind, byId) {
  const ac = row.ac || byId.get(row.id) || fidsRowCache.get(row.id);
  if (!ac) return null;
  fidsRowCache.set(row.id, ac);
  if (kind === "arr" && row.lateMin != null) fidsArrLate.set(row.id, row.lateMin);
  let st = kind === "arr" ? normalizeFidsArrSt(row.st, row.lateMin) : row.st;
  if (kind === "arr" && row.latched && !byId.has(row.id)) st = ["ARRIVED", "arrived"];
  fidsStat[kind].set(row.id, st);
  if (row.latched) fidsArrLatch.set(row.id, true);
  return ac;
}

function renderFids(own, traffic) {
  const ap = playerFidsIcao();
  fidsAp = ap;
  if (!ap || !own) {
    depList = [];
    arrList = [];
    if (aptTab !== "info") setAptTab("info");
    else applyAptTab();
    renderEnrouteTraffic();
    return;
  }
  const board = lastFids[ap] || { dep: [], arr: [] };
  const pool = traffic || [];
  const ownAc = fidsOwnAc(own, pool);
  const byId = new Map(pool.map((ac) => [ac.id, ac]));
  if (ownAc) byId.set(ownAc.id, ownAc);
  fidsStat.dep.clear();
  fidsStat.arr.clear();
  fidsArrLatch.clear();
  fidsArrLate.clear();
  depList = board.dep.map((row) => hydrateFidsRow(row, "dep", byId)).filter(Boolean);
  arrList = board.arr.map((row) => hydrateFidsRow(row, "arr", byId)).filter(Boolean);
  const live = new Set([...depList, ...arrList].map((ac) => ac.id));
  for (const id of [...fidsRowCache.keys()]) {
    if (!live.has(id)) fidsRowCache.delete(id);
  }
  fidsOwnId = ownAc?.id;
  applyAptTab();
  if (aptTab === "dep" || aptTab === "arr") renderFidsTable();
}

function paintFidsSelection() {
  const body = document.getElementById("fids-body");
  if (!body) return;
  for (const tr of body.querySelectorAll("tr[data-id]")) {
    const id = tr.getAttribute("data-id");
    const own = Boolean(fidsOwnId && id === fidsOwnId);
    tr.classList.toggle("is-own", own);
    tr.classList.toggle("is-sel", !own && id === selectedId);
  }
}

function renderFidsTable() {
  const kind = aptTab === "arr" ? "arr" : "dep";
  const fidsBox = document.getElementById("apt-fids");
  const y = fidsBox && !fidsBox.hidden ? fidsBox.scrollTop : 0;
  if (fidsBox) fidsBox.dataset.kind = kind;
  const list = kind === "arr" ? arrList : depList;
  const th = document.getElementById("fids-place-th");
  if (th) th.textContent = kind === "dep" ? "To" : "From";
  const empty = kind === "dep" ? "No departures" : "No arrivals";
  paintFidsBody(list, kind, empty);
  if (fidsBox && !fidsBox.hidden) {
    if (fidsScrollToNow) {
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          scrollFidsToNow(fidsBox, list, kind);
          fidsScrollToNow = false;
        });
      });
    } else {
      fidsBox.scrollTop = y;
    }
  }
}

function paintFidsBody(slice, kind, empty) {
  const body = document.getElementById("fids-body");
  if (!body) return;
  if (!slice.length) {
    body.innerHTML = `<tr><td colspan="6">${empty}</td></tr>`;
    return;
  }
  const prev = new Map();
  for (const tr of body.querySelectorAll("tr[data-id]")) {
    prev.set(tr.getAttribute("data-id"), tr);
  }
  const frag = document.createDocumentFragment();
  for (const ac of slice) {
    const html = fidsRow(ac, kind, fidsOwnId);
    const old = prev.get(ac.id);
    const wrap = document.createElement("tbody");
    wrap.innerHTML = html;
    const next = wrap.firstElementChild;
    if (old) {
      const oldImg = old.querySelector("img.fids-logo");
      const newImg = next.querySelector("img.fids-logo");
      if (oldImg && newImg && oldImg.getAttribute("src") === newImg.getAttribute("src")) {
        newImg.replaceWith(oldImg);
      }
      if (old.innerHTML === next.innerHTML && old.className === next.className) {
        frag.appendChild(old);
        continue;
      }
    }
    frag.appendChild(next);
  }
  body.replaceChildren(frag);
}

function fitFidsTable() {
  if (aptTab !== "dep" && aptTab !== "arr") return;
  renderFidsTable();
}
