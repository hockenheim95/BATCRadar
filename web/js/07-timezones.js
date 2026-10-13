function tzFor(ac, kind) {
  return String(ac[`${kind}Tz`] || "").trim();
}

function utcOffsetText(tz, date = new Date()) {
  if (!tz) return "";
  try {
    const parts = Object.fromEntries(
      new Intl.DateTimeFormat("en-US", {
        timeZone: tz,
        hourCycle: "h23",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      })
        .formatToParts(date)
        .map((part) => [part.type, part.value])
    );
    const asUtc = Date.UTC(
      Number(parts.year),
      Number(parts.month) - 1,
      Number(parts.day),
      Number(parts.hour),
      Number(parts.minute),
      Number(parts.second)
    );
    const mins = Math.round((asUtc - date.getTime()) / 60000);
    const sign = mins >= 0 ? "+" : "-";
    const abs = Math.abs(mins);
    const hours = Math.floor(abs / 60);
    const minutes = String(abs % 60).padStart(2, "0");
    return `UTC ${sign}${hours}:${minutes}`;
  } catch {
    return "";
  }
}

/** AI / default: nearest matching weekday in BATC string. */
function parseBatcZ(text) {
  const m = /([A-Za-z]{3})\s+(\d{1,2}):(\d{2})Z/.exec(text || "");
  if (!m) return null;
  const days = { sun: 0, mon: 1, tue: 2, wed: 3, thu: 4, fri: 5, sat: 6 };
  const want = days[m[1].toLowerCase()];
  if (want == null) return null;
  const now = new Date();
  const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), Number(m[2]), Number(m[3])));
  let diff = want - d.getUTCDay();
  if (diff > 3) diff -= 7;
  if (diff < -3) diff += 7;
  d.setUTCDate(d.getUTCDate() + diff);
  return d;
}

function parseBatcZForAc(ac, text) {
  return parseBatcZ(text);
}

function formatSchedTime(d, tz) {
  try {
    const parts = Object.fromEntries(
      new Intl.DateTimeFormat("en-US", {
        timeZone: tz || "UTC",
        hour: "numeric",
        minute: "2-digit",
        hour12: true,
      })
        .formatToParts(d)
        .filter((p) => p.type !== "dayPeriod" && p.type !== "literal")
        .map((p) => [p.type, p.value])
    );
    if (parts.hour == null || parts.minute == null) return "";
    return `${parts.hour}:${parts.minute}`;
  } catch {
    return "";
  }
}

function formatSched(text, tz, ac) {
  const d = ac ? parseBatcZForAc(ac, text) : parseBatcZ(text);
  if (!d) return { time: "", offset: "" };
  if (!tz) {
    return {
      time: formatSchedTime(d, "UTC"),
      offset: "Z",
    };
  }
  const time = formatSchedTime(d, tz);
  const off = new Intl.DateTimeFormat("en-US", {
    timeZone: tz,
    timeZoneName: "shortOffset",
    hour: "numeric",
  })
    .formatToParts(d)
    .find((part) => part.type === "timeZoneName");
  return { time, offset: (off?.value || "Z").replace("GMT", "UTC") };
}

function setSchedTime(id, text, tz) {
  const ids = [id, `${id}-phone`];
  const d = parseBatcZ(text);
  for (const fullId of ids) {
    const el = document.getElementById(fullId);
    if (!el) continue;
    el.replaceChildren();
    if (!d) {
      el.textContent = "—";
      continue;
    }
    const { time } = formatSched(text, tz, lastOwn);
    el.append(document.createTextNode(time || "—"));
    const off = utcOffsetText(tz, d);
    if (!off) continue;
    const span = document.createElement("span");
    span.className = "tz-off";
    span.textContent = `(${off})`;
    el.append(span);
  }
}

function setAirportTimes(ac) {
  setSchedTime("p-dep-time", ac.timeFrom, tzFor(ac, "origin"));
  setSchedTime("p-arr-time", ac.timeTo, tzFor(ac, "dest"));
}
