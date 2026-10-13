function wxKind(code) {
  const c = Number(code);
  if (!Number.isFinite(c)) return "";
  if (c === 0 || c === 1) return "clear";
  if (c === 2) return "partly";
  if (c === 3) return "cloud";
  if (c === 45 || c === 48) return "fog";
  if (c >= 51 && c <= 57) return "drizzle";
  if ((c >= 61 && c <= 67) || (c >= 80 && c <= 82)) return "rain";
  if ((c >= 71 && c <= 77) || c === 85 || c === 86) return "snow";
  if (c >= 95) return "storm";
  return "cloud";
}

function wxClass(kind) {
  if (kind === "clear" || kind === "partly") return "wx-clear";
  if (kind === "rain" || kind === "drizzle") return "wx-rain";
  if (kind === "snow") return "wx-snow";
  if (kind === "storm") return "wx-storm";
  if (kind === "fog") return "wx-fog";
  return "wx-cloud";
}

function wxSvg(kind) {
  const sun = `<circle cx="12" cy="12" r="3.6" fill="currentColor"/><g stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><path d="M12 2.2v2.2M12 19.6v2.2M2.2 12h2.2M19.6 12h2.2M4.7 4.7l1.6 1.6M17.7 17.7l1.6 1.6M19.3 4.7l-1.6 1.6M6.3 17.7l-1.6 1.6"/></g>`;
  const cloud = `<path fill="currentColor" d="M7.2 18.2h9.4a3.3 3.3 0 0 0 .4-6.6 4.6 4.6 0 0 0-8.8-1.3 3.1 3.1 0 0 0-1 7.9z"/>`;
  const drops = `<g stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M8 19.2v1.6M12 19.6v2M16 19.2v1.6"/></g>`;
  const flakes = `<g stroke="currentColor" stroke-width="1.4" stroke-linecap="round"><path d="M8 18.6v2.6M6.7 19.9h2.6M16 18.6v2.6M14.7 19.9h2.6"/></g>`;
  const bolt = `<path fill="currentColor" d="M13.2 15.6h3.2l-4.2 6.2 1-3.6H10l3.2-2.6z"/>`;
  const body = {
    clear: sun,
    partly: `<g transform="translate(4 -3) scale(.62)">${sun}</g><g transform="translate(0 3)">${cloud}</g>`,
    cloud,
    fog: `<g stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><path d="M4 8h16M6 12h12M4 16h16M7 20h10"/></g>`,
    drizzle: `${cloud}<g transform="translate(0 .4)">${drops}</g>`,
    rain: cloud + drops,
    snow: cloud + flakes,
    storm: `${cloud}${bolt}`,
  }[kind] || cloud;
  return `<svg viewBox="0 0 24 24" aria-hidden="true">${body}</svg>`;
}

function fmtTempC(tempC) {
  const c = Number(tempC);
  if (!Number.isFinite(c)) return "";
  if (useMetricUnits()) return `${Math.round(c)} °C`;
  return `${Math.round((c * 9) / 5 + 32)} °F`;
}

const wxMem = new Map();
let wxStamp = "";
let wxOriginData = null;
let wxDestData = null;

function wxCacheKey(ll) {
  if (!ll || !Number.isFinite(ll[0]) || !Number.isFinite(ll[1])) return "";
  return `${Number(ll[0]).toFixed(2)},${Number(ll[1]).toFixed(2)}`;
}

function cachedWx(ll) {
  const key = wxCacheKey(ll);
  if (!key) return null;
  const hit = wxMem.get(key);
  return hit ? hit.data : null;
}

async function loadWx(ll) {
  const key = wxCacheKey(ll);
  if (!key) return null;
  const hit = wxMem.get(key);
  if (hit && Date.now() - hit.at < 8 * 60 * 1000) return hit.data;
  const res = await fetch(`/api/weather?lat=${encodeURIComponent(ll[0])}&lon=${encodeURIComponent(ll[1])}`);
  if (!res.ok) return hit ? hit.data : null;
  const data = await res.json();
  wxMem.set(key, { at: Date.now(), data });
  return data;
}

function paintAirportWx() {
  for (const [id, data] of [["p-origin-wx", wxOriginData], ["p-dest-wx", wxDestData]]) {
    const el = document.getElementById(id);
    if (!el) continue;
    const kind = data ? wxKind(data.code) : "";
    const temp = data ? fmtTempC(data.tempC) : "";
    const key = `${kind}|${temp}`;
    if (el.dataset.wx === key) continue;
    const prevKind = el.dataset.wxKind || "";
    if (!kind && !temp) {
      if (!el.innerHTML) el.hidden = true;
      continue;
    }
    el.dataset.wx = key;
    el.dataset.wxKind = kind;
    el.hidden = false;
    if (prevKind === kind && el.querySelector(".apt-wx-temp")) {
      el.querySelector(".apt-wx-temp").textContent = temp;
      continue;
    }
    const ico = kind ? `<span class="apt-wx-ico ${wxClass(kind)}">${wxSvg(kind)}</span>` : "";
    el.innerHTML = `${ico}<span class="apt-wx-temp">${temp}</span>`;
  }
}

function refreshAirportWx(ac) {
  const origin = originLL(ac);
  const dest = destLL(ac);
  const stamp = `${wxCacheKey(origin)}|${wxCacheKey(dest)}`;
  if (stamp === wxStamp) return;
  wxStamp = stamp;
  const nextOrigin = cachedWx(origin);
  const nextDest = cachedWx(dest);
  if (nextOrigin) wxOriginData = nextOrigin;
  if (nextDest) wxDestData = nextDest;
  paintAirportWx();
  const ticket = stamp;
  Promise.all([loadWx(origin), loadWx(dest)])
    .then(([originWx, destWx]) => {
      if (wxStamp !== ticket) return;
      wxOriginData = originWx;
      wxDestData = destWx;
      paintAirportWx();
    })
    .catch(() => {});
}

let logoKey = "";
let logoSeq = 0;

