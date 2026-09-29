"""Decision & confidence gate (blueprint sections 06 and 12).

Three jobs:
  1. Confidence: a weighted score from live signals, not a constant. Each factor is 0..1.
  2. Autonomy mode: MANUAL / SUPERVISED / AUTONOMOUS. The mode drops at once when confidence falls,
     climbs back one level after HEALTHY_TICKS_TO_CLIMB healthy ticks, and only an operator can
     re-arm AUTONOMOUS after it was lost.
  3. The gate itself: for one recommendation, which legs are blocked by guardrails, whether a human
     must approve, and whether it may be executed at all (never on stale data).

Pure functions and one small state machine, no I/O, so the rules are easy to test and to explain.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Literal

from app.contracts import AllocationLeg, ComponentHealth, DecisionRecord, NetworkSnapshot, Recommendation, TwinFuture

Mode = Literal["MANUAL", "SUPERVISED", "AUTONOMOUS"]
RANK: dict[str, int] = {"MANUAL": 0, "SUPERVISED": 1, "AUTONOMOUS": 2}

# name -> (label, weight). Weights sum to 1.0 and match the blueprint's example weights.
WEIGHTS: dict[str, tuple[str, float]] = {
    "fit": ("Forecast fit", 0.25),
    "twin": ("Twin accuracy", 0.20),
    "fresh": ("Data freshness", 0.20),
    "normal": ("Demand normality", 0.15),
    "health": ("Component health", 0.10),
    "crisis": ("No active crisis", 0.10),
}

AUTONOMOUS_MIN = 0.80
MANUAL_BELOW = 0.60
HEALTHY_TICKS_TO_CLIMB = 3
TWIN_SAMPLES = 20
TWIN_PRIOR = 0.85  # before the first verified decision


@dataclass(frozen=True)
class Guardrails:
    """Limits that hold in every mode. Values are placeholders to tune during rehearsal."""
    max_auto_leg_litres: float = 5000.0      # no single auto-executed leg above this
    routine_total_litres: float = 6000.0     # above this a decision is "large" and goes to a human in SUPERVISED
    depot_reserve_fraction: float = 0.10     # never draw a depot below 10% of its capacity for that fuel
    max_legs_per_tick: int = 8               # rate limit on what can auto-execute per tick


# ---------------------------------------------------------------------------------------------
# Recommendation shape helpers. The fixture and the intelligence service fill different fields
# (futures vs twin_futures, candidates vs legs); read both.
# ---------------------------------------------------------------------------------------------

def selected_legs(rec: Recommendation) -> list[AllocationLeg]:
    if rec.legs:
        return list(rec.legs)
    for c in rec.candidates:
        if c.id == rec.selected_candidate_id:
            return list(c.legs)
    return []


def futures_of(rec: Recommendation) -> list[TwinFuture]:
    return list(rec.futures or rec.twin_futures)


def constraints_of(rec: Recommendation) -> list[str]:
    return list(rec.constraints or rec.constraints_applied)


def policy_of(rec: Recommendation) -> str:
    return rec.versions.get("policy") or rec.policy or rec.selected_candidate_id


# ---------------------------------------------------------------------------------------------
# 1. Confidence
# ---------------------------------------------------------------------------------------------

def twin_accuracy(records: list[DecisionRecord]) -> float | None:
    """1 - mean relative Twin error over the most recent verified decisions (backend outcome check).

    Relative to max(actual, predicted, 500 L) so tiny volumes don't dominate. None before any sample.
    """
    checks = [r.twin_check for r in records if r.twin_check][:TWIN_SAMPLES]
    if not checks:
        return None
    rel = [c["error_l"] / max(c["actual_l"], c["predicted_l"], 500.0) for c in checks]
    return max(0.0, 1.0 - sum(rel) / len(rel))


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def compute_factors(snap: NetworkSnapshot | None, rec: Recommendation | None,
                    components: list[ComponentHealth] | None = None,
                    twin_acc: float | None = None) -> dict[str, float]:
    """Every factor is 0..1, where 1 is good. Each one is traceable to something an operator can see."""
    signals = rec.signals if rec else []
    fallbacks = set(rec.fallback_used) if rec else set()

    # Forecast fit: demand anomalies mean the forecast is missing reality; the fallback predictor is coarser.
    anomalies = [s for s in signals if s.kind in ("demand_anomaly", "demand_spike", "persistent_demand_drift")]
    fit = 0.92 - sum(0.25 if s.severity == "crit" else 0.15 for s in anomalies)
    if "forecaster" in fallbacks:
        fit = min(fit, 0.62)

    twin = twin_acc if twin_acc is not None else TWIN_PRIOR

    # Freshness: stale data or a non-closed circuit means we are not looking at the present.
    fresh = 1.0
    if snap is None or snap.is_stale or (rec is not None and rec.built_on_stale_data):
        fresh = 0.0
    elif snap.freshness is not None and snap.freshness.circuit == "HALF_OPEN":
        fresh = 0.5

    normal = 1.0 - sum(0.6 if s.severity == "crit" else 0.3 if s.severity == "warn" else 0.0 for s in anomalies)

    comps = [c for c in (components or []) if c.status != "unknown"]
    health = (sum(1.0 if c.status == "healthy" else 0.5 if c.status == "degraded" else 0.0 for c in comps)
              / len(comps)) if comps else 1.0
    if fallbacks - {"forecaster"}:
        health = min(health, 0.75)
    if "forecaster" in fallbacks:
        health = min(health, 0.5)

    active = [e for e in (snap.events if snap else []) if e.status == "ACTIVE"]
    crisis = 1.0 if not active else max(0.4, 1.0 - 0.2 * len(active))
    if rec is not None and rec.mode == "containment":
        crisis = min(crisis, 0.4)

    return {k: round(_clamp(v), 3) for k, v in
            {"fit": fit, "twin": twin, "fresh": fresh, "normal": normal, "health": health, "crisis": crisis}.items()}


def confidence(factors: dict[str, float]) -> float:
    return round(sum(WEIGHTS[k][1] * factors.get(k, 0.0) for k in WEIGHTS), 3)


def target_mode(factors: dict[str, float]) -> Mode:
    c = confidence(factors)
    if factors.get("fresh", 0) < 0.5 or c < MANUAL_BELOW:
        return "MANUAL"
    if c < AUTONOMOUS_MIN or factors.get("crisis", 0) < 1 or factors.get("health", 0) < 1:
        return "SUPERVISED"
    return "AUTONOMOUS"


# ---------------------------------------------------------------------------------------------
# 2. Autonomy state machine
# ---------------------------------------------------------------------------------------------

@dataclass
class AutonomyController:
    mode: Mode = "SUPERVISED"
    armed: bool = False            # operator has allowed AUTONOMOUS
    healthy_ticks: int = 0
    last_tick: int | None = None
    confidence: float = 0.0
    factors: dict[str, float] = field(default_factory=dict)
    log: deque = field(default_factory=lambda: deque(maxlen=50))

    def _note(self, tick: int | None, message: str) -> None:
        if self.log and self.log[0]["message"] == message:
            self.log[0]["tick"] = tick
        else:
            self.log.appendleft({"tick": tick, "message": message})

    def observe(self, tick: int, factors: dict[str, float]) -> Mode:
        """Feed the latest factors. Downgrades are immediate; upgrades need new, healthy ticks."""
        self.factors, self.confidence = factors, confidence(factors)
        target = target_mode(factors)
        new_tick = self.last_tick is None or tick > self.last_tick
        self.last_tick = tick if new_tick else self.last_tick

        if RANK[target] < RANK[self.mode]:
            self._note(tick, f"Mode {self.mode} -> {target} (confidence {self.confidence:.2f}).")
            if self.mode == "AUTONOMOUS":
                self.armed = False
            self.mode, self.healthy_ticks = target, 0
            return self.mode
        if not new_tick:
            return self.mode
        if RANK[target] > RANK[self.mode]:
            if self.mode == "SUPERVISED" and not self.armed:
                self.healthy_ticks = 0
                self._note(tick, "Healthy, but Autonomous needs an operator to re-arm.")
                return self.mode
            self.healthy_ticks += 1
            if self.healthy_ticks >= HEALTHY_TICKS_TO_CLIMB:
                up: Mode = "SUPERVISED" if self.mode == "MANUAL" else "AUTONOMOUS"
                self._note(tick, f"{HEALTHY_TICKS_TO_CLIMB} healthy ticks. Mode {self.mode} -> {up}.")
                self.mode, self.healthy_ticks = up, 0
            else:
                self._note(tick, f"Healthy tick {self.healthy_ticks}/{HEALTHY_TICKS_TO_CLIMB} toward the next level.")
        else:
            self.healthy_ticks = 0
        return self.mode

    def rearm(self, by: str = "operator") -> tuple[bool, str]:
        if not self.factors or target_mode(self.factors) != "AUTONOMOUS":
            msg = f"Re-arm refused: confidence {self.confidence:.2f}, conditions for Autonomous not met."
            self._note(self.last_tick, msg)
            return False, msg
        self.armed = True
        if self.mode == "SUPERVISED":
            self.mode = "AUTONOMOUS"
            msg = f"{by} re-armed Autonomous."
        else:
            msg = f"{by} re-armed Autonomous; mode steps up after {HEALTHY_TICKS_TO_CLIMB} healthy ticks."
        self._note(self.last_tick, msg)
        return True, msg

    def force(self, mode: Mode, by: str = "operator") -> str:
        """An operator may always step down (e.g. to MANUAL for a demo). Stepping up goes through rearm()."""
        if RANK[mode] >= RANK[self.mode]:
            return f"Mode stays {self.mode}; use re-arm to go up."
        if self.mode == "AUTONOMOUS":
            self.armed = False
        msg = f"{by} set mode {self.mode} -> {mode}."
        self.mode, self.healthy_ticks = mode, 0
        self._note(self.last_tick, msg)
        return msg

    def view(self) -> dict:
        return {"mode": self.mode, "armed": self.armed, "healthy_ticks": self.healthy_ticks,
                "confidence": self.confidence, "target_mode": target_mode(self.factors) if self.factors else None,
                "factors": [{"key": k, "label": WEIGHTS[k][0], "weight": WEIGHTS[k][1],
                             "value": self.factors.get(k)} for k in WEIGHTS],
                "thresholds": {"autonomous_min": AUTONOMOUS_MIN, "manual_below": MANUAL_BELOW,
                               "healthy_ticks_to_climb": HEALTHY_TICKS_TO_CLIMB},
                "log": list(self.log)}


# ---------------------------------------------------------------------------------------------
# 3. The gate
# ---------------------------------------------------------------------------------------------

@dataclass
class GateResult:
    requires_human: bool
    executable: bool
    auto_execute: bool
    reasons: list[str]
    blocked_legs: list[dict]
    mode: Mode
    confidence: float

    def as_dict(self) -> dict:
        return {"requires_human": self.requires_human, "executable": self.executable,
                "auto_execute": self.auto_execute, "reasons": self.reasons, "blocked_legs": self.blocked_legs,
                "mode": self.mode, "confidence": self.confidence}


def check_legs(snap: NetworkSnapshot, legs: list[AllocationLeg], rails: Guardrails) -> list[dict]:
    """Guardrails that hold in every mode. Returns one entry per blocked leg."""
    routes, stations, depots = snap.route_map, snap.station_map, snap.depot_map
    drawn: dict[tuple[str, str], float] = {}
    blocked = []
    for i, leg in enumerate(legs):
        route = routes.get(leg.route_id)
        station = stations.get(leg.station_id or (route.station_id if route else ""))
        depot = depots.get(leg.source_depot_id or (route.depot_id if route else ""))
        why = None
        if route is None:
            why = f"unknown route {leg.route_id}"
        elif route.status == "DISRUPTED":
            why = f"{leg.route_id} is DISRUPTED"
        elif station is not None and station.status != "OPEN":
            why = f"{station.id} is in {station.status}"
        elif depot is not None:
            key = (depot.id, leg.fuel_type)
            drawn[key] = drawn.get(key, 0.0) + leg.quantity
            reserve = rails.depot_reserve_fraction * depot.capacity.get(leg.fuel_type, 0.0)
            if depot.inventory.get(leg.fuel_type, 0.0) - drawn[key] < reserve - 1e-6:
                why = f"{depot.id} {leg.fuel_type} would fall below its {reserve:,.0f} L reserve"
        if why:
            blocked.append({"index": i, "route_id": leg.route_id, "reason": why})
    return blocked


def evaluate(rec: Recommendation, snap: NetworkSnapshot | None, mode: Mode, conf: float,
             rails: Guardrails | None = None) -> GateResult:
    rails = rails or Guardrails()
    legs = selected_legs(rec)
    reasons: list[str] = []
    executable = True

    stale = snap is None or snap.is_stale or rec.built_on_stale_data
    if stale:
        executable = False
        reasons.append("built on stale data: recommend only, cannot be executed")
    blocked = check_legs(snap, legs, rails) if snap is not None else []
    reasons += [f"guardrail: {b['reason']}" for b in blocked]
    if legs and len(blocked) == len(legs):
        executable = False

    human = False
    if mode == "MANUAL":
        human = True
        reasons.append("mode MANUAL: every action needs a human")
    if conf < AUTONOMOUS_MIN:
        human = True
        reasons.append(f"confidence {conf:.2f} < {AUTONOMOUS_MIN:.2f}")
    if rec.mode == "containment":
        human = True
        reasons.append("containment decision: always needs a human")
    big = [leg for leg in legs if leg.quantity > rails.max_auto_leg_litres]
    if big:
        human = True
        reasons.append(f"{len(big)} leg(s) above the {rails.max_auto_leg_litres:,.0f} L auto limit")
    total = sum(leg.quantity for leg in legs)
    if mode == "SUPERVISED" and total > rails.routine_total_litres:
        human = True
        reasons.append(f"mode SUPERVISED: {total:,.0f} L total is above the routine "
                       f"{rails.routine_total_litres:,.0f} L")
    if len(legs) > rails.max_legs_per_tick:
        human = True
        reasons.append(f"{len(legs)} legs exceed the per-tick rate limit of {rails.max_legs_per_tick}")
    active = [e.type for e in (snap.events if snap else []) if e.status == "ACTIVE"]
    if active and mode != "AUTONOMOUS":
        reasons.append("active event: " + ", ".join(sorted(set(active))))
    if rec.fallback_used:
        reasons.append("fallback in use: " + ", ".join(rec.fallback_used))
    if blocked:
        human = True

    auto = executable and not human and bool(legs) and mode != "MANUAL"
    return GateResult(requires_human=human, executable=executable, auto_execute=auto, reasons=reasons,
                      blocked_legs=blocked, mode=mode, confidence=conf)
