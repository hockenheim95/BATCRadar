let tickBusy = false;
let lastBatcSession = 0;
let lastTrafficSeq = 0;
let lastTrafficAt = 0;
let serverReachable = true;
const TRAFFIC_STALE_MS = 3500;

function paintTrafficBadge(data) {
  const badge = document.getElementById("mode-badge");
  if (!badge) return;
  if (!data) {
    badge.textContent = "unconnected";
    badge.classList.toggle("live", false);
    badge.classList.toggle("unconnected", true);
    badge.classList.toggle("stale", false);
    return;
  }
  const batcLive = data.trafficSource === "batc";
  const seq = Number(data.trafficSeq) || 0;
  const at = Number(data.trafficAt) || 0;
  if (seq && seq !== lastTrafficSeq) {
    lastTrafficSeq = seq;
    lastTrafficAt = at || Date.now();
  } else if (!lastTrafficAt && at) {
    lastTrafficAt = at;
  }
  const stale =
    batcLive && lastTrafficAt > 0 && Date.now() - lastTrafficAt > TRAFFIC_STALE_MS;
  if (stale) {
    badge.textContent = "stale";
    badge.classList.toggle("live", false);
    badge.classList.toggle("unconnected", false);
    badge.classList.toggle("stale", true);
    return;
  }
  badge.textContent = batcLive ? "LIVE" : "unconnected";
  badge.classList.toggle("live", batcLive);
  badge.classList.toggle("unconnected", !batcLive);
  badge.classList.toggle("stale", false);
}

function clearTrafficOnServerDown() {
  if (!serverReachable) return;
  serverReachable = false;
  paintTrafficBadge(null);
  for (const [id, marker] of markers) {
    map.removeLayer(marker);
    markers.delete(id);
    seenOnField.delete(id);
    leftFieldAt.delete(id);
    forgetMotion(id);
  }
  lastTraffic = [];
  lastFids = {};
  lastOwn = null;
  lastTrafficSeq = 0;
  lastTrafficAt = 0;
  lastBatcSession = 0;
  if (typeof lastPanelIdentityKey !== "undefined") lastPanelIdentityKey = "";
  lastHistAt = 0;
  forgetAllTracks();
  if (typeof motionById !== "undefined") motionById.clear();
  if (typeof stopPaintMotion === "function") stopPaintMotion();
  hideHistory();
  ownRoute.setLatLngs([]);
  selRoute.setLatLngs([]);
  closePanel();
}

async function tick() {
  if (tickBusy || document.hidden) return;
  tickBusy = true;
  try {
    const res = await fetch("/api/traffic", { cache: "no-store" });
    if (!res.ok) throw new Error(`traffic ${res.status}`);
    const data = await res.json();
    serverReachable = true;
    if (data.version) {
      const verMeta = document.querySelector('meta[name="efb-version"]');
      if (verMeta) verMeta.content = data.version;
    }
    paintTrafficBadge(data);

    const batcSession = Number(data.batcSession) || 0;
    if (batcSession !== lastBatcSession) {
      const prevSession = lastBatcSession;
      lastBatcSession = batcSession;
      if (prevSession) {
        const watching = trailAcId;
        forgetAllTracks();
        lastHistAt = 0;
        hideHistory();
        if (watching && batcSession) {
          const ac =
            (markers.has(watching) && markers.get(watching).acData) ||
            (data.aircraft || []).find((row) => row.id === watching) ||
            null;
          if (ac) armTrail(ac);
        }
      }
    }

    lastFids = data.trafficSource === "batc" ? data.fids || {} : {};
    lastTraffic = data.aircraft || [];

    const seen = new Set();
    for (const ac of data.aircraft) {
      const rolled = noteFlightLeg(ac);
      if (rolled) lastHistAt = 0;
      noteFieldAltFromAc(ac);
      if (!mapVisible(ac)) {
        if (markers.has(ac.id)) {
          map.removeLayer(markers.get(ac.id));
          markers.delete(ac.id);
          seenOnField.delete(ac.id);
          leftFieldAt.delete(ac.id);
          forgetMotion(ac.id);
        }
        const trailLocked = trailAcId === ac.id;
        if (
          selectedId === ac.id &&
          !trailLocked &&
          !(fidsPinnedAp && fidsBoardHasId(fidsPinnedAp, ac.id))
        ) {
          selRoute.setLatLngs([]);
          closePanel();
        }
        continue;
      }
      seen.add(ac.id);
      noteMotion(ac);
      if (!markers.has(ac.id)) {
        const marker = L.marker([ac.lat, ac.lon], {
          icon: iconFor(ac, ac.id === selectedId),
          zIndexOffset: ac.isOwnship ? 1000 : 0,
        });
        marker.iconKey = iconKey(ac, ac.id === selectedId);
        marker.on("click", (ev) => {
          L.DomEvent.stop(ev);
          const current = marker.acData || ac;
          if (current.isOwnship) {
            // Selection only — follow me stays off unless the user presses Follow me.
            clearFidsPin();
            openPanel(current);
            armTrail(current);
            syncRoutes(current);
            bumpChrome();
            return;
          }
          setFollowMe(false);
          if (typeof followAcId !== "undefined" && followAcId && followAcId !== current.id) {
            clearFollowTraffic();
          }
          const prev = selectedId;
          clearFidsPin();
          openPanel(current);
          armTrail(current);
          if (prev && markers.has(prev)) refreshIcon(markers.get(prev));
          refreshIcon(marker);
          ownRoute.setLatLngs([]);
          syncRoutes(shownAc(marker) || current);
          bumpChrome();
        });
        marker.acData = ac;
        marker.addTo(map);
        markers.set(ac.id, marker);
      } else {
        const marker = markers.get(ac.id);
        marker.acData = ac;
        const key = iconKey(ac, ac.id === selectedId);
        if (marker.iconKey !== key) refreshIcon(marker);
      }
      if (selectedId === ac.id) {
        setLiveStats(ac);
        if (typeof refreshSelectedPanelIfNeeded === "function") refreshSelectedPanelIfNeeded(ac);
      }
      noteFieldLeave(ac);
    }

    const liveIds = new Set(data.aircraft.map((ac) => ac.id));
    for (const id of [...lastFlightLegs.keys()]) {
      if (!liveIds.has(id)) lastFlightLegs.delete(id);
    }

    for (const [id, marker] of markers) {
      if (!seen.has(id)) {
        map.removeLayer(marker);
        markers.delete(id);
        seenOnField.delete(id);
        leftFieldAt.delete(id);
        forgetTrack(id);
        forgetMotion(id);
        if (selectedId === id && !(fidsPinnedAp && fidsBoardHasId(fidsPinnedAp, id))) {
          selRoute.setLatLngs([]);
          closePanel();
        }
        if (trailAcId === id) hideHistory();
      }
    }
    const you = data.ownship;
    const firstOwn = Boolean(you && !lastOwn);
    const jumpedFar = Boolean(you && lastOwn && kmBetween([lastOwn.lat, lastOwn.lon], [you.lat, you.lon]) > 80);
    if (jumpedFar) {
      hideHistory();
      forgetAllTracks();
      lastHistAt = 0;
      if (you && you.id) resetMotion(you.id, you);
    }
    if (you) {
      lastOwn = you;
      noteFieldAltFromAc(you);
      noteMotion(you);
      if (typeof refreshSelectedPanelIfNeeded === "function") refreshSelectedPanelIfNeeded(you);
    } else if (lastOwn) {
      forgetMotion(lastOwn.id);
      lastOwn = null;
      ownRoute.setLatLngs([]);
      if (!watchingOther()) closePanel();
    }
    if (you && (firstOwn || jumpedFar) && !followMe) focusOwnshipOnMap(you);
    if (you && (firstOwn || jumpedFar) && followMe) {
      followPausedUntil = 0;
      panToMapCenter(you.lat, you.lon, { animate: false });
    }
    followOwnshipIfNeeded();
    const now = Date.now();
    const boxed = liveStatsAc() || lastOwn;
    if (boxed && !document.getElementById("panel").hidden) {
      syncAirportBox(boxed);
    }
    if (boxed && !document.getElementById("panel").hidden && lastOwn) {
      if (now - lastFidsAt > 2000) {
        lastFidsAt = now;
        renderFids(lastOwn, lastTraffic);
      } else {
        applyAptTab();
      }
    }
    if (!watchingOther() && lastOwn) {
      if (atAirport(lastOwn)) {
        if (panelMode !== "flight" || document.getElementById("panel").hidden) {
          showAirportBoard(lastOwn, lastTraffic);
        }
      } else if (panelMode !== "flight" || document.getElementById("panel").hidden || selectedId !== lastOwn.id) {
        openPanel(lastOwn);
      }
    }
    if (typeof fitPhonePanelHeight === "function") fitPhonePanelHeight();
    const routeAc =
      selectedId && markers.has(selectedId)
        ? markers.get(selectedId).acData
        : lastOwn;
    if (routeAc && typeof applyMotionNow === "function") applyMotionNow(routeAc);
    syncRoutes(routeAc);
    let histAc = trailAcId && markers.has(trailAcId) ? markers.get(trailAcId).acData : null;
    if (!histAc && trailAcId) histAc = lastTraffic.find((row) => row.id === trailAcId) || null;
    if (histAc && now - lastHistAt > (TOUCH_UI ? 2800 : 700)) {
      lastHistAt = now;
      if (typeof applyMotionNow === "function") applyMotionNow(histAc);
      showHistory(histAc);
    }
    if (typeof prefetchVisibleTracks === "function") prefetchVisibleTracks(data.aircraft);
    if (typeof ensurePaintMotion === "function") ensurePaintMotion();
  } catch {
    clearTrafficOnServerDown();
  } finally {
    tickBusy = false;
  }
}
