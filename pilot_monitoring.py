"""A320-style Pilot Monitoring: phases, callouts, and alerts from ownship samples."""

from __future__ import annotations

import time
from typing import Any


PHASES = (
    "parked",
    "pushback",
    "taxi",
    "takeoff",
    "climb",
    "cruise",
    "descent",
    "approach",
    "landing",
    "rollout",
    "unknown",
)

PHASE_LABEL = {
    "parked": "Parked",
    "pushback": "Pushback",
    "taxi": "Taxi",
    "takeoff": "Takeoff",
    "climb": "Climb",
    "cruise": "Cruise",
    "descent": "Descent",
    "approach": "Approach",
    "landing": "Landing",
    "rollout": "Rollout",
    "unknown": "—",
}

CHECKLIST_HINT = {
    "pushback": "Before pushback / after start",
    "taxi": "Before taxi",
    "takeoff": "Before takeoff",
    "climb": "After takeoff",
    "approach": "Approach",
    "landing": "Landing",
    "rollout": "After landing",
}


def _f(val: Any, default: float = 0.0) -> float:
    try:
        n = float(val)
    except (TypeError, ValueError):
        return default
    return n if n == n else default  # NaN guard


def _speed_kt(sample: dict) -> float:
    ias = _f(sample.get("ias"))
    if ias > 30:
        return ias
    return _f(sample.get("gs"))


def _agl_ft(sample: dict) -> float:
    agl = _f(sample.get("agl"), default=-1.0)
    if agl >= 0:
        return agl
    if sample.get("onGround"):
        return 0.0
    return max(0.0, _f(sample.get("alt")))


def _vs_fpm(sample: dict, prev: dict | None) -> float:
    if sample.get("vs") is not None:
        return _f(sample.get("vs"))
    if not prev:
        return 0.0
    dt = _f(sample.get("_t")) - _f(prev.get("_t"))
    if dt < 0.2:
        return _f(prev.get("vs"))
    dalt = _f(sample.get("alt")) - _f(prev.get("alt"))
    return dalt / dt * 60.0


def detect_phase(sample: dict, vs: float, agl: float, speed: float) -> str:
    st = str(sample.get("state") or "").lower()
    status = str(sample.get("status") or "").lower()
    on_ground = bool(sample.get("onGround") or sample.get("simOnGround"))
    parked_brake = bool(sample.get("parkingBrake"))
    at_dest = bool(sample.get("atDestination"))

    if on_ground and (parked_brake or st == "parked" or (speed < 1.5 and st not in ("taxi", "pushback", "takeoff"))):
        if st == "pushback" or "push" in status:
            return "pushback"
        return "parked"
    if on_ground and (st == "pushback" or "push" in status):
        return "pushback"
    if on_ground and speed >= 55:
        return "takeoff" if not at_dest else "rollout"
    if on_ground and at_dest and speed >= 25:
        return "rollout"
    if on_ground:
        return "taxi" if st in ("taxi", "takeoff", "") or speed >= 1.5 else "parked"

    if agl < 80 and vs < -100:
        return "landing"
    if (
        "approach" in status
        or "cleared to land" in status
        or "landing" in status
        or st in ("approach", "landing")
        or (agl < 3500 and vs < -200 and speed < 220)
    ):
        return "approach"
    if vs < -500:
        return "descent"
    if vs > 400 or (agl < 12000 and speed < 280 and vs > 150):
        return "climb"
    if agl > 8000 or _f(sample.get("alt")) > 10000:
        return "cruise"
    return "climb" if vs >= 0 else "descent"


class PilotMonitor:
    """Stateful PM for one ownship session."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.phase = "unknown"
        self._fired: set[str] = set()
        self._prev: dict | None = None
        self._log: list[dict] = []
        self._alert_at: dict[str, float] = {}
        self._session_alt0: float | None = None

    def update(self, plane: dict | None, now: float | None = None) -> dict:
        if not plane:
            self.reset()
            return self._payload([])

        t = time.monotonic() if now is None else float(now)
        sample = dict(plane)
        sample["_t"] = t
        if self._session_alt0 is None and sample.get("onGround"):
            self._session_alt0 = _f(sample.get("alt"))

        speed = _speed_kt(sample)
        agl = _agl_ft(sample)
        if agl <= 0 and not sample.get("onGround") and self._session_alt0 is not None:
            agl = max(0.0, _f(sample.get("alt")) - self._session_alt0)
        vs = _vs_fpm(sample, self._prev)
        sample["vs"] = vs
        sample["agl"] = agl
        sample["speedKt"] = speed

        phase = detect_phase(sample, vs, agl, speed)
        events: list[dict] = []

        if phase != self.phase:
            prev_phase = self.phase
            self.phase = phase
            if prev_phase != "unknown":
                events.append(self._evt("phase", f"{PHASE_LABEL.get(phase, phase)}", "info"))
            hint = CHECKLIST_HINT.get(phase)
            if hint and phase not in ("climb", "cruise", "descent"):
                key = f"chk:{phase}"
                if key not in self._fired:
                    self._fired.add(key)
                    events.append(self._evt(key, f"Checklist: {hint}", "hint"))
            if phase in ("climb", "cruise", "descent", "approach"):
                # Allow landing callouts again on next approach.
                self._fired -= {k for k in self._fired if k.startswith("ra:") or k in ("minimums", "retard", "pos_climb", "gear_up")}
            if phase == "takeoff":
                self._fired -= {k for k in ("80kt", "rotate", "pos_climb", "gear_up") if k in self._fired}

        events.extend(self._takeoff_callouts(sample, speed, vs, agl))
        events.extend(self._altitude_callouts(sample, agl, vs, phase))
        events.extend(self._config_callouts(sample, phase))
        events.extend(self._alerts(sample, speed, vs, agl, phase, t))

        for ev in events:
            self._log.append(ev)
        if len(self._log) > 40:
            self._log = self._log[-40:]

        self._prev = sample
        return self._payload(events)

    def _payload(self, fresh: list[dict]) -> dict:
        return {
            "phase": self.phase,
            "phaseLabel": PHASE_LABEL.get(self.phase, self.phase),
            "checklist": CHECKLIST_HINT.get(self.phase, ""),
            "callouts": list(self._log),
            "fresh": fresh,
            "speedKt": None if not self._prev else round(_f(self._prev.get("speedKt")), 1),
            "vs": None if not self._prev else round(_f(self._prev.get("vs"))),
            "agl": None if not self._prev else round(_f(self._prev.get("agl"))),
            "ias": None if not self._prev else (round(_f(self._prev.get("ias"))) if self._prev.get("ias") is not None else None),
            "flaps": None if not self._prev else self._prev.get("flaps"),
            "gearDown": None if not self._prev else self._prev.get("gearDown"),
        }

    def _evt(self, key: str, text: str, kind: str = "callout") -> dict:
        return {"id": key, "text": text, "kind": kind, "t": time.time()}

    def _takeoff_callouts(self, sample: dict, speed: float, vs: float, agl: float) -> list[dict]:
        out: list[dict] = []
        on_ground = bool(sample.get("onGround") or sample.get("simOnGround"))
        if on_ground and speed >= 80 and "80kt" not in self._fired:
            self._fired.add("80kt")
            out.append(self._evt("80kt", "Eighty knots"))
        if on_ground and speed >= 135 and "rotate" not in self._fired:
            self._fired.add("rotate")
            out.append(self._evt("rotate", "Rotate"))
        if (not on_ground) and vs > 200 and agl > 30 and "pos_climb" not in self._fired:
            self._fired.add("pos_climb")
            out.append(self._evt("pos_climb", "Positive climb"))
            if "gear_up" not in self._fired and sample.get("gearDown") is not False:
                self._fired.add("gear_up")
                out.append(self._evt("gear_up", "Gear up", "hint"))
        return out

    def _altitude_callouts(self, sample: dict, agl: float, vs: float, phase: str) -> list[dict]:
        out: list[dict] = []
        alt = _f(sample.get("alt"))
        on_ground = bool(sample.get("onGround") or sample.get("simOnGround"))
        if on_ground:
            return out

        if alt >= 10000 and "alt10000" not in self._fired and phase in ("climb", "cruise"):
            self._fired.add("alt10000")
            out.append(self._evt("alt10000", "Ten thousand", "info"))

        descending = vs < -150 or phase in ("descent", "approach", "landing")
        if not descending:
            return out

        bands = (
            (1000, "ra:1000", "One thousand"),
            (500, "ra:500", "Five hundred"),
            (200, "minimums", "Minimums"),
            (50, "ra:50", "Fifty"),
            (40, "ra:40", "Forty"),
            (30, "ra:30", "Thirty"),
            (20, "ra:20", "Twenty"),
            (10, "ra:10", "Ten"),
        )
        for limit, key, text in bands:
            if agl <= limit and key not in self._fired:
                self._fired.add(key)
                out.append(self._evt(key, text))
        if agl <= 20 and "retard" not in self._fired and phase in ("landing", "approach"):
            self._fired.add("retard")
            out.append(self._evt("retard", "Retard"))
        return out

    def _config_callouts(self, sample: dict, phase: str) -> list[dict]:
        out: list[dict] = []
        prev = self._prev
        if not prev:
            return out
        flaps = sample.get("flaps")
        prev_flaps = prev.get("flaps")
        if flaps is not None and prev_flaps is not None and int(flaps) != int(prev_flaps):
            key = f"flaps:{int(flaps)}"
            out.append(self._evt(key, f"Flaps {int(flaps)}", "info"))
        gear = sample.get("gearDown")
        prev_gear = prev.get("gearDown")
        if gear is not None and prev_gear is not None and bool(gear) != bool(prev_gear):
            text = "Gear down" if gear else "Gear up"
            out.append(self._evt(f"gear:{int(bool(gear))}", text, "info"))
        if phase == "approach" and sample.get("gearDown") is False and _agl_ft(sample) < 2000:
            key = "gear_reminder"
            if key not in self._fired:
                self._fired.add(key)
                out.append(self._evt(key, "Gear?", "alert"))
        return out

    def _alerts(
        self,
        sample: dict,
        speed: float,
        vs: float,
        agl: float,
        phase: str,
        now: float,
    ) -> list[dict]:
        out: list[dict] = []

        def cool(key: str, sec: float = 8.0) -> bool:
            last = self._alert_at.get(key, 0.0)
            if now - last < sec:
                return False
            self._alert_at[key] = now
            return True

        if phase in ("approach", "landing") and agl < 2500 and vs < -2200 and cool("sink"):
            out.append(self._evt("sink", "Sink rate", "alert"))
        if phase in ("approach", "landing") and agl < 2000 and speed > 190 and cool("speed"):
            out.append(self._evt("speed", "Speed", "alert"))
        if phase == "climb" and agl < 1000 and sample.get("gearDown") is True and cool("gear_late", 12.0):
            out.append(self._evt("gear_late", "Gear", "alert"))
        return out
