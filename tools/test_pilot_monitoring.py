#!/usr/bin/env python3
"""Unit tests for A320-style Pilot Monitoring callouts."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pilot_monitoring import PilotMonitor, detect_phase


def _texts(pm_out: dict) -> list[str]:
    return [e["text"] for e in pm_out.get("fresh") or []]


def test_takeoff_callouts() -> None:
    pm = PilotMonitor()
    t = 1000.0
    pm.update({"onGround": True, "gs": 10, "alt": 100, "state": "taxi", "parkingBrake": False}, now=t)
    out = pm.update({"onGround": True, "gs": 85, "ias": 85, "alt": 100, "state": "takeoff"}, now=t + 1)
    assert "Eighty knots" in _texts(out), out
    out = pm.update({"onGround": True, "gs": 140, "ias": 140, "alt": 100, "state": "takeoff"}, now=t + 2)
    assert "Rotate" in _texts(out), out
    out = pm.update(
        {"onGround": False, "gs": 160, "ias": 160, "alt": 200, "agl": 80, "vs": 1200, "gearDown": True},
        now=t + 3,
    )
    texts = _texts(out)
    assert "Positive climb" in texts, texts
    assert "Gear up" in texts, texts


def test_landing_ra_callouts() -> None:
    pm = PilotMonitor()
    t = 2000.0
    pm.update({"onGround": False, "gs": 140, "alt": 3000, "agl": 2800, "vs": -800, "state": "flight"}, now=t)
    assert pm.phase in ("descent", "approach", "climb", "cruise")
    out = pm.update(
        {"onGround": False, "gs": 135, "alt": 1200, "agl": 900, "vs": -700, "status": "on approach"},
        now=t + 5,
    )
    assert "One thousand" in _texts(out) or any("thousand" in x.lower() for x in _texts(out)), out
    out = pm.update(
        {"onGround": False, "gs": 130, "alt": 450, "agl": 180, "vs": -600, "status": "cleared to land"},
        now=t + 10,
    )
    texts = _texts(out)
    assert "Minimums" in texts or "Five hundred" in texts or "Fifty" in texts, texts


def test_sink_rate_alert() -> None:
    pm = PilotMonitor()
    t = 3000.0
    pm.update(
        {"onGround": False, "gs": 150, "alt": 1500, "agl": 1200, "vs": -500, "status": "on approach"},
        now=t,
    )
    out = pm.update(
        {"onGround": False, "gs": 155, "alt": 800, "agl": 600, "vs": -2800, "status": "on approach"},
        now=t + 2,
    )
    assert "Sink rate" in _texts(out), out


def test_phase_parked_to_taxi() -> None:
    assert detect_phase({"onGround": True, "parkingBrake": True, "state": "parked"}, 0, 0, 0) == "parked"
    assert detect_phase({"onGround": True, "state": "taxi", "parkingBrake": False}, 0, 0, 12) == "taxi"
    assert detect_phase({"onGround": True, "state": "takeoff"}, 0, 0, 90) == "takeoff"


def main() -> int:
    test_phase_parked_to_taxi()
    test_takeoff_callouts()
    test_landing_ra_callouts()
    test_sink_rate_alert()
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
