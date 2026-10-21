/** Pilot Monitoring HUD: phase, callout feed, optional speech. */

const PM_VOICE_KEY = "efb-pm-voice";
const PM_ENABLED_KEY = "efb-pm";
const PM_SEEN_MAX = 80;

let pmSeen = new Set();
let pmSpeechQueue = [];
let pmSpeaking = false;

function pmEnabled() {
  return localStorage.getItem(PM_ENABLED_KEY) !== "0";
}

function pmVoiceOn() {
  return localStorage.getItem(PM_VOICE_KEY) !== "0";
}

function setPmEnabled(on) {
  localStorage.setItem(PM_ENABLED_KEY, on ? "1" : "0");
  const root = document.getElementById("pm-hud");
  if (root) root.hidden = !on;
  if (!on) {
    pmSeen.clear();
    window.speechSynthesis?.cancel();
    pmSpeechQueue = [];
    pmSpeaking = false;
  }
}

function setPmVoice(on) {
  localStorage.setItem(PM_VOICE_KEY, on ? "1" : "0");
  if (!on) {
    window.speechSynthesis?.cancel();
    pmSpeechQueue = [];
    pmSpeaking = false;
  }
}

function pmEsc(text) {
  return String(text || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function pmKindClass(kind) {
  if (kind === "alert") return "pm-line--alert";
  if (kind === "hint") return "pm-line--hint";
  if (kind === "info") return "pm-line--info";
  return "pm-line--callout";
}

function speakPm(text) {
  if (!pmVoiceOn() || !window.speechSynthesis || !text) return;
  pmSpeechQueue.push(String(text));
  drainPmSpeech();
}

function drainPmSpeech() {
  if (pmSpeaking || !pmSpeechQueue.length || !window.speechSynthesis) return;
  if (!pmVoiceOn()) {
    pmSpeechQueue = [];
    return;
  }
  pmSpeaking = true;
  const text = pmSpeechQueue.shift();
  const utter = new SpeechSynthesisUtterance(text);
  utter.rate = 1.05;
  utter.pitch = 1;
  utter.lang = "en-GB";
  utter.onend = () => {
    pmSpeaking = false;
    drainPmSpeech();
  };
  utter.onerror = () => {
    pmSpeaking = false;
    drainPmSpeech();
  };
  window.speechSynthesis.speak(utter);
}

function paintPmHud(pm) {
  const root = document.getElementById("pm-hud");
  if (!root || !pmEnabled()) {
    if (root) root.hidden = true;
    return;
  }
  root.hidden = false;

  const phaseEl = document.getElementById("pm-phase");
  const checkEl = document.getElementById("pm-checklist");
  const liveEl = document.getElementById("pm-live");
  const logEl = document.getElementById("pm-log");
  const metaEl = document.getElementById("pm-meta");

  if (phaseEl) phaseEl.textContent = pm.phaseLabel || "—";
  if (checkEl) {
    checkEl.textContent = pm.checklist || "";
    checkEl.hidden = !pm.checklist;
  }

  const bits = [];
  if (pm.speedKt != null) bits.push(`${Math.round(pm.speedKt)} kt`);
  if (pm.vs != null) {
    const sign = pm.vs > 0 ? "+" : "";
    bits.push(`${sign}${Math.round(pm.vs)} fpm`);
  }
  if (pm.agl != null) bits.push(`AGL ${Math.round(pm.agl)} ft`);
  if (pm.flaps != null) bits.push(`Flaps ${pm.flaps}`);
  if (pm.gearDown === true) bits.push("Gear ↓");
  if (pm.gearDown === false) bits.push("Gear ↑");
  if (metaEl) metaEl.textContent = bits.join(" · ") || "Waiting for ownship…";

  const fresh = Array.isArray(pm.fresh) ? pm.fresh : [];
  for (const ev of fresh) {
    const token = `${ev.id}:${ev.t || ""}:${ev.text || ""}`;
    if (pmSeen.has(token)) continue;
    pmSeen.add(token);
    if (pmSeen.size > PM_SEEN_MAX) {
      pmSeen = new Set([...pmSeen].slice(-Math.floor(PM_SEEN_MAX / 2)));
    }
    if (ev.kind === "callout" || ev.kind === "alert") speakPm(ev.text);
  }

  const latest = fresh.length ? fresh[fresh.length - 1] : (pm.callouts || []).slice(-1)[0];
  if (liveEl) {
    if (latest) {
      liveEl.textContent = latest.text;
      liveEl.className = `pm-live ${pmKindClass(latest.kind)}`;
    } else {
      liveEl.textContent = "Monitoring…";
      liveEl.className = "pm-live pm-line--info";
    }
  }

  if (logEl) {
    const rows = (pm.callouts || []).slice(-12).reverse();
    logEl.innerHTML = rows
      .map((ev) => `<div class="pm-line ${pmKindClass(ev.kind)}">${pmEsc(ev.text)}</div>`)
      .join("");
  }
}

function clearPmHud() {
  paintPmHud({
    phaseLabel: "—",
    checklist: "",
    callouts: [],
    fresh: [],
    speedKt: null,
    vs: null,
    agl: null,
  });
}

function updatePilotMonitoring(ownship) {
  if (!pmEnabled()) {
    const root = document.getElementById("pm-hud");
    if (root) root.hidden = true;
    return;
  }
  if (!ownship || !ownship.pm) {
    clearPmHud();
    return;
  }
  paintPmHud(ownship.pm);
}

function initPilotMonitoringUi() {
  const en = document.getElementById("opt-pm");
  const voice = document.getElementById("opt-pm-voice");
  if (en) {
    en.checked = pmEnabled();
    en.addEventListener("change", () => {
      setPmEnabled(en.checked);
      if (typeof bumpChrome === "function") bumpChrome();
    });
  }
  if (voice) {
    voice.checked = pmVoiceOn();
    voice.addEventListener("change", () => {
      setPmVoice(voice.checked);
      if (typeof bumpChrome === "function") bumpChrome();
    });
  }
  const root = document.getElementById("pm-hud");
  if (root) root.hidden = !pmEnabled();
}

initPilotMonitoringUi();
