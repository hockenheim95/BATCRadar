let tickBusy = false;
let lastBatcSession = 0;
async function tick() {
  if (tickBusy || document.hidden) return;
  tickBusy = true;
  try {
    const res = await fetch("/api/traffic", { cache: "no-store" });
    const data = await res.json();
    if (data.version) {
      const verMeta = document.querySelector('meta[name="efb-version"]');
      if (verMeta) verMeta.content = data.version;
    }
    const badge = document.getElementById("mode-badge");
    const batcLive = data.trafficSource === "batc";
    badge.textContent = batcLive ? "LIVE" : "unconnected";
    badge.classList.toggle("live", batcLive);
    badge.classList.toggle("unconnected", !batcLive);

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

    lastFids = data.fids || {};
    lastTraffic = data.aircraft || [];

    const seen = new Set();
    for (const ac of data.aircraft) {
      const rolled = noteFlightLeg(ac);
      if (rolled) lastHistAt = 0;
      noteLocalTrack(ac);
      if (!mapVisible(ac)) {
        if (markers.has(ac.id)) {
          map.removeLayer(markers.get(ac.id));
          markers.delete(ac.id);
          seenOnField.delete(ac.id);
          leftFieldAt.delete(ac.id);
          forgetMotion(ac.id);
        }
        if (selectedId === ac.id && !(fidsPinnedAp && fidsBoardHasId(fidsPinnedAp, ac.id))) {
          selRoute.setLatLngs([]);
          closePanel();
        }
        if (trailAcId === ac.id) hideHistory();
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
            setFollowMe(true);
            clearFidsPin();
            openPanel(current);
            armTrail(current);
            syncRoutes(current);
            bumpChrome();
            return;
          }
          setFollowMe(false);
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
      noteMotion(you);
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
  } finally {
    tickBusy = false;
  }
}
