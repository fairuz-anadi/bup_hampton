"""Shared contracts between the three lanes (backend, intelligence, frontend/copilot).

This file is the single source of truth. The frontend generates its TypeScript types from the
backend's OpenAPI schema, and the JSON fixtures in fixtures/ follow these shapes.

Change a model here only after the team agrees on it.

Sections:
  1. Simulator entities   validated copies of what /v1/* returns
  2. NetworkSnapshot      Anadi -> Turjo, Samprity
  3. Forecast             Turjo's forecaster service  (POST /forecast)
  4. Recommendation       Turjo -> Samprity
  5. DecisionRecord       audit snapshot, persisted by the backend
  6. Explanation          backend -> Samprity's copilot
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    # The simulator may add fields in a later image; accept and ignore them rather than fail.
    model_config = ConfigDict(extra="ignore")


# ---------------------------------------------------------------------------------------------
# 1. Simulator entities
# ---------------------------------------------------------------------------------------------

class FuelType(StrEnum):
    DIESEL = "DIESEL"
    PETROL = "PETROL"
    OCTANE = "OCTANE"


FUELS: tuple[FuelType, ...] = (FuelType.DIESEL, FuelType.PETROL, FuelType.OCTANE)

# Litres per fuel type. Every depot and station reports all three.
FuelLitres = dict[FuelType, float]


class Instance(_Model):
    id: int
    scenario_id: str
    scenario_version: str
    seed: int
    sim_time: datetime
    tick: int = Field(ge=0)
    tick_minutes: int = Field(gt=0)
    status: Literal["PAUSED", "RUNNING"]


class Region(_Model):
    id: str
    name: str
    demand_factor: float = Field(gt=0)


class Depot(_Model):
    id: str
    name: str
    region_id: str
    # CONSTRAINED is a label only: we verified it does not lower dispatch_capacity_per_tick.
    status: Literal["OPEN", "CONSTRAINED"]
    dispatch_capacity_per_tick: float = Field(ge=0)
    capacity: FuelLitres
    inventory: FuelLitres


class Station(_Model):
    id: str
    name: str
    region_id: str
    status: Literal["OPEN", "OUTAGE"]
    demand_profile: str
    demand_multiplier: float = Field(ge=0)
    capacity: FuelLitres
    inventory: FuelLitres


class Route(_Model):
    id: str
    source_depot_id: str
    destination_station_id: str
    transit_ticks: int = Field(ge=0)
    max_shipment: float = Field(gt=0)
    status: Literal["AVAILABLE", "DISRUPTED"]


class SupplyArrival(_Model):
    id: str
    depot_id: str
    fuel_type: FuelType
    quantity: float = Field(ge=0)
    planned_tick: int
    actual_tick: int | None = None
    status: Literal["SCHEDULED", "DELAYED", "ARRIVED"]


EventType = Literal["demand_spike", "route_disruption", "station_outage", "depot_constraint",
                    "shipment_delay", "supply_shortfall"]


class SimEvent(_Model):
    id: int
    type: EventType
    start_tick: int
    end_tick: int
    status: Literal["SCHEDULED", "ACTIVE", "RESOLVED"]
    parameters: dict = Field(default_factory=dict)


AllocationStatus = Literal["PENDING", "IN_TRANSIT", "ARRIVED", "FAILED", "CANCELLED"]


class Allocation(_Model):
    id: int
    idempotency_key: str
    source_depot_id: str
    destination_station_id: str
    route_id: str
    fuel_type: FuelType
    quantity: float
    created_tick: int
    departure_tick: int | None = None
    expected_arrival_tick: int | None = None
    actual_arrival_tick: int | None = None
    status: AllocationStatus
    failure_reason: str | None = None


class DemandObservation(_Model):
    id: int
    station_id: str
    fuel_type: FuelType
    tick: int
    sim_time: datetime
    demand_liters: float = Field(ge=0)
    served_liters: float = Field(ge=0)
    unmet_liters: float = Field(ge=0)


class SimMetrics(_Model):
    served_demand_liters: float
    unmet_demand_liters: float
    service_level: float = Field(ge=0, le=1)
    allocation_liters: float
    allocation_failures: int


# ---------------------------------------------------------------------------------------------
# 2. NetworkSnapshot
# ---------------------------------------------------------------------------------------------

class InTransitLeg(_Model):
    """One PENDING or IN_TRANSIT allocation, as seen by the station it is heading to."""
    allocation_id: int
    route_id: str
    source_depot_id: str
    station_id: str
    fuel_type: FuelType
    quantity: float
    status: Literal["PENDING", "IN_TRANSIT"]
    expected_arrival_tick: int | None


class ResourceFreshness(_Model):
    fetched_at: datetime | None = Field(description="Wall time of the last successful fetch.")
    age_seconds: float | None
    stale: bool = Field(description="True if the last fetch failed or carried X-Simulator-Stale.")
    last_error: str | None = None


class Freshness(_Model):
    stale: bool = Field(description="Any resource stale, or the simulator circuit is open.")
    reasons: list[str] = Field(default_factory=list)
    circuit: Literal["CLOSED", "OPEN", "HALF_OPEN"]
    resources: dict[str, ResourceFreshness]


class NetworkSnapshot(_Model):
    """Everything the intelligence layer and the UI need about the world at one tick.

    The backend refreshes this from the simulator every tick. Reads never hit the simulator.
    If a refresh fails, the last good data is kept and `freshness` says how old it is.
    """
    tick: int
    sim_time: datetime
    tick_minutes: int
    sim_status: Literal["PAUSED", "RUNNING"]
    scenario_id: str
    seed: int
    built_at: datetime
    freshness: Freshness
    regions: list[Region]
    depots: list[Depot]
    stations: list[Station]
    routes: list[Route]
    supply_arrivals: list[SupplyArrival]
    events: list[SimEvent]
    in_transit: list[InTransitLeg] = Field(description="PENDING + IN_TRANSIT allocations.")
    metrics: SimMetrics
    # Litres per station -> fuel still to arrive. Precomputed from in_transit for convenience.
    in_transit_totals: dict[str, dict[FuelType, float]]
    # Litres already committed from each depot this tick (counts against dispatch capacity).
    dispatched_this_tick: dict[str, float]


# ---------------------------------------------------------------------------------------------
# 3. Forecast  (Turjo's forecaster service)
# ---------------------------------------------------------------------------------------------

class ForecastRequest(_Model):
    tick: int
    horizon_ticks: int = Field(gt=0, le=192)
    station_ids: list[str] | None = Field(None, description="None means every station.")


class ForecastSeries(_Model):
    station_id: str
    fuel_type: FuelType
    mean: list[float] = Field(description="Litres per tick, index 0 = the next tick.")
    p10: list[float]
    p90: list[float]
    residual_sigma: float


class ForecastResponse(_Model):
    model_version: str
    generated_at_tick: int
    horizon_ticks: int
    series: list[ForecastSeries]
    fallback: bool = Field(False, description="True when produced by the in-process fallback predictor.")


# ---------------------------------------------------------------------------------------------
# 4. Recommendation  (Turjo -> Samprity)
# ---------------------------------------------------------------------------------------------

class AllocationLeg(_Model):
    """One POST /v1/allocations. Legs above route.max_shipment are split before they get here."""
    route_id: str
    source_depot_id: str
    station_id: str
    fuel_type: FuelType
    quantity: float = Field(gt=0)


class Signal(_Model):
    """Why an area is at risk. Shown to operators and handed to the copilot."""
    kind: Literal["demand_anomaly", "depletion", "route_disrupted", "station_outage", "depot_constrained",
                  "supply_delayed", "supply_reduced", "stockout_risk", "stale_data", "other"]
    severity: Literal["info", "warn", "crit"]
    message: str
    station_id: str | None = None
    fuel_type: FuelType | None = None
    value: float | None = None


class RiskItem(_Model):
    station_id: str
    fuel_type: FuelType
    hours_to_stockout: float | None = Field(description="None = no stockout inside the horizon.")
    p_stockout: float = Field(ge=0, le=1)
    has_backup_route: bool


class TwinFuture(_Model):
    """One projected future. Always a projection, never ground truth."""
    candidate_id: str
    label: str
    horizon_ticks: int
    network_unmet_liters: float
    unmet_by_station: dict[str, float]
    first_stockout_tick: int | None
    service_level: float = Field(ge=0, le=1)
    notes: list[str] = Field(default_factory=list, description="e.g. 'Tongi short later'.")


class Candidate(_Model):
    id: str = Field(description="'noop', 'greedy-v1', 'lp-v2', ...")
    policy: str
    legs: list[AllocationLeg]


class Recommendation(_Model):
    id: str
    tick: int
    created_at: datetime
    mode: Literal["prevention", "containment"]
    candidates: list[Candidate]
    selected_candidate_id: str
    futures: list[TwinFuture] = Field(default_factory=list, description="Empty if the Twin was unavailable.")
    risks: list[RiskItem]
    signals: list[Signal]
    constraints: list[str] = Field(default_factory=list, description="Constraints that were binding.")
    confidence: float = Field(ge=0, le=1)
    versions: dict[str, str] = Field(default_factory=dict, description="policy, forecast_model, deployment")
    fallback_used: list[str] = Field(default_factory=list, description="Components that ran on fallback.")
    built_on_stale_data: bool = False


# ---------------------------------------------------------------------------------------------
# 5. DecisionRecord  (audit snapshot)
# ---------------------------------------------------------------------------------------------

class SubmittedAllocation(_Model):
    leg: AllocationLeg
    idempotency_key: str
    http_status: int | None
    sim_allocation_id: int | None = None
    result: Literal["accepted", "rejected", "held", "skipped"]
    error_code: str | None = None
    message: str | None = None


class DecisionRecord(_Model):
    """One important recommendation through its whole lifecycle. Grows stage by stage."""
    decision_id: str
    sim_tick: int
    created_at: datetime
    stage: Literal["observed", "predicted", "candidates", "projected", "gated", "approved", "rejected",
                   "submitted", "outcome", "verified"]
    versions: dict[str, str] = Field(default_factory=dict)
    mode: Literal["MANUAL", "SUPERVISED", "AUTONOMOUS"] | None = None
    recommendation: Recommendation | None = None
    gate: dict | None = Field(None, description="{'requires_human': bool, 'reasons': [...]}")
    approval: dict | None = Field(None, description="{'decision': 'approved'|'rejected', 'by': str, 'reason': str}")
    submissions: list[SubmittedAllocation] = Field(default_factory=list)
    outcome: dict | None = None
    twin_check: dict | None = Field(None, description="{'predicted_l', 'actual_l', 'error_l'}")


# ---------------------------------------------------------------------------------------------
# 6. Explanation  (backend -> copilot)
# ---------------------------------------------------------------------------------------------

class ExplainRequest(_Model):
    kind: Literal["decision", "incident", "network_summary", "incident_report"]
    facts: dict = Field(description="Structured facts only. The copilot must not invent numbers.")


class ExplainResponse(_Model):
    text: str
    cited_facts: list[str] = Field(default_factory=list)
    source: Literal["llm", "template"]


# ---------------------------------------------------------------------------------------------
# Backend API bodies
# ---------------------------------------------------------------------------------------------

class SubmitAllocationsRequest(_Model):
    decision_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._-]+$")
    legs: list[AllocationLeg] = Field(min_length=1, max_length=50)


class SubmitAllocationsResponse(_Model):
    decision_id: str
    submissions: list[SubmittedAllocation]


class CreateDecisionRequest(_Model):
    recommendation: Recommendation
    gate: dict | None = None
    mode: Literal["MANUAL", "SUPERVISED", "AUTONOMOUS"] | None = None


class ReviewRequest(_Model):
    by: str = Field(min_length=1, max_length=60)
    reason: str | None = Field(None, max_length=500)
    legs: list[AllocationLeg] | None = Field(None, max_length=50,
                                             description="Approve only: modified legs replace the selected candidate.")


class ChaosEventRequest(_Model):
    type: EventType
    start_tick: int | None = Field(None, ge=0, description="Absolute tick. Omit to use start_in_ticks.")
    start_in_ticks: int = Field(0, ge=0, le=1000, description="Relative to the current tick.")
    duration_ticks: int = Field(gt=0, le=2000)
    parameters: dict = Field(default_factory=dict)


class ChaosFaultRequest(_Model):
    type: Literal["latency", "unavailable", "error_rate", "stale_data", "stream_disconnect"]
    duration_seconds: int = Field(gt=0, le=3600)
    parameters: dict = Field(default_factory=dict)


class PacerRequest(_Model):
    enabled: bool
    interval_ms: int = Field(1000, ge=100, le=10000)
    max_ticks: int | None = Field(None, gt=0, le=10000)


class PolicyRequest(_Model):
    policy: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9.-]+$")
    accept: bool = False
    by: str = Field("operator", max_length=60)


class ComponentHealth(_Model):
    name: str
    status: Literal["healthy", "degraded", "down", "unknown"]
    detail: str | None = None


class HealthReport(_Model):
    status: Literal["healthy", "degraded", "down"]
    components: list[ComponentHealth]
    tick: int | None
    snapshot_age_seconds: float | None
    version: str = "dev"
    active_policy: str | None = None
    pacer_running: bool = False
