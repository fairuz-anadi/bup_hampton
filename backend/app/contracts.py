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

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    name: str = ""
    region_id: str = ""
    # CONSTRAINED is a label only: we verified it does not lower dispatch_capacity_per_tick.
    status: Literal["OPEN", "CONSTRAINED", "CLOSED"] = "OPEN"
    dispatch_capacity_per_tick: float = Field(ge=0, default=12000.0)
    capacity: FuelLitres = Field(default_factory=dict)
    inventory: FuelLitres = Field(default_factory=dict)

    @property
    def region(self) -> str:
        return self.region_id


class Station(_Model):
    id: str
    name: str = ""
    region_id: str = ""
    status: Literal["OPEN", "OUTAGE"] = "OPEN"
    demand_profile: str = "urban_high"
    demand_multiplier: float = Field(ge=0, default=1.0)
    capacity: FuelLitres = Field(default_factory=dict)
    inventory: FuelLitres = Field(default_factory=dict)

    @property
    def region(self) -> str:
        return self.region_id


class Route(_Model):
    id: str
    source_depot_id: str = ""
    destination_station_id: str = ""
    transit_ticks: int = Field(ge=0, default=2)
    max_shipment: float = Field(gt=0, default=5000.0)
    status: Literal["AVAILABLE", "DISRUPTED"] = "AVAILABLE"

    @model_validator(mode="before")
    @classmethod
    def _map_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "depot_id" in data and "source_depot_id" not in data:
                data["source_depot_id"] = data["depot_id"]
            if "station_id" in data and "destination_station_id" not in data:
                data["destination_station_id"] = data["station_id"]
        return data

    @property
    def depot_id(self) -> str:
        return self.source_depot_id

    @property
    def station_id(self) -> str:
        return self.destination_station_id


class SupplyArrival(_Model):
    id: str
    depot_id: str
    fuel_type: FuelType = FuelType.PETROL
    quantity: float = Field(ge=0, default=10000.0)
    planned_tick: int = 0
    actual_tick: int | None = None
    arrival_tick: int | None = None
    status: Literal["SCHEDULED", "DELAYED", "ARRIVED", "CANCELLED"] = "SCHEDULED"

    @model_validator(mode="before")
    @classmethod
    def _map_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "fuel" in data and "fuel_type" not in data:
                data["fuel_type"] = data["fuel"]
            if "arrival_tick" in data and "actual_tick" not in data:
                data["actual_tick"] = data["arrival_tick"]
            if "actual_tick" in data and "arrival_tick" not in data:
                data["arrival_tick"] = data["actual_tick"]
        return data

    @property
    def fuel(self) -> FuelType:
        return self.fuel_type


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
    allocation_id: int = 1
    route_id: str
    source_depot_id: str = ""
    station_id: str = ""
    fuel_type: FuelType = FuelType.PETROL
    quantity: float = 0.0
    status: Literal["PENDING", "IN_TRANSIT"] = "IN_TRANSIT"
    expected_arrival_tick: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _map_in_transit_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "depot_id" in data and "source_depot_id" not in data:
                data["source_depot_id"] = data["depot_id"]
            if "fuel" in data and "fuel_type" not in data:
                data["fuel_type"] = data["fuel"]
            if "arrival_tick" in data and "expected_arrival_tick" not in data:
                data["expected_arrival_tick"] = data["arrival_tick"]
            if "allocation_id" not in data:
                data["allocation_id"] = 1
            if "status" not in data:
                data["status"] = "IN_TRANSIT"
        return data

    @property
    def depot_id(self) -> str:
        return self.source_depot_id

    @property
    def fuel(self) -> FuelType:
        return self.fuel_type

    @property
    def arrival_tick(self) -> int:
        return self.expected_arrival_tick if self.expected_arrival_tick is not None else 0


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
    tick: int = 0
    sim_time: datetime | str | None = None
    tick_minutes: int = 15
    sim_status: Literal["PAUSED", "RUNNING"] = "RUNNING"
    scenario_id: str = "bup-scenario-1"
    seed: int = 42
    built_at: datetime | None = None
    freshness: Freshness | None = None
    regions: list[Region] = Field(default_factory=list)
    depots: list[Depot] = Field(default_factory=list)
    stations: list[Station] = Field(default_factory=list)
    routes: list[Route] = Field(default_factory=list)
    supply_arrivals: list[SupplyArrival] = Field(default_factory=list)
    events: list[SimEvent] = Field(default_factory=list)
    in_transit: list[InTransitLeg] = Field(default_factory=list, description="PENDING + IN_TRANSIT allocations.")
    metrics: SimMetrics | None = None
    # Litres per station -> fuel still to arrive. Precomputed from in_transit for convenience.
    in_transit_totals: dict[str, dict[FuelType, float]] = Field(default_factory=dict)
    # Litres already committed from each depot this tick (counts against dispatch capacity).
    dispatched_this_tick: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _map_snapshot_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if isinstance(data.get("depots"), dict):
                data["depots"] = list(data["depots"].values())
            if isinstance(data.get("stations"), dict):
                data["stations"] = list(data["stations"].values())
            if isinstance(data.get("routes"), dict):
                data["routes"] = list(data["routes"].values())
            if "status" in data and "sim_status" not in data:
                data["sim_status"] = data["status"]
            if "active_events" in data and "events" not in data:
                data["events"] = data["active_events"]
            if "sim_time" in data and isinstance(data["sim_time"], str):
                pass
        return data

    @property
    def station_map(self) -> dict[str, Station]:
        return {s.id: s for s in self.stations}

    @property
    def depot_map(self) -> dict[str, Depot]:
        return {d.id: d for d in self.depots}

    @property
    def route_map(self) -> dict[str, Route]:
        return {r.id: r for r in self.routes}

    @property
    def is_stale(self) -> bool:
        return self.freshness.stale if self.freshness else False

    @property
    def active_events(self) -> list[SimEvent]:
        return self.events

    @property
    def status(self) -> str:
        return self.sim_status

    @active_events.setter
    def active_events(self, val: list[SimEvent]):
        self.events = val

# ---------------------------------------------------------------------------------------------
# 3. Forecast  (Turjo's forecaster service)
# ---------------------------------------------------------------------------------------------

class ForecastRequest(_Model):
    tick: int = 0
    horizon_ticks: int = Field(24, gt=0, le=192)
    station_ids: list[str] | None = Field(None, description="None means every station.")
    station_id: str = ""
    fuel: FuelType = FuelType.PETROL
    current_tick: int = 0
    demand_multiplier: float = 1.0
    demand_history: list[dict] | None = None


class ForecastSeries(_Model):
    station_id: str
    fuel_type: FuelType
    mean: list[float] = Field(description="Litres per tick, index 0 = the next tick.")
    p10: list[float]
    p90: list[float]
    residual_sigma: float


class ForecastResponse(_Model):
    model_version: str = "fc-v1"
    generated_at_tick: int = 0
    horizon_ticks: int = 24
    series: list[ForecastSeries] = Field(default_factory=list)
    fallback: bool = Field(False, description="True when produced by the in-process fallback predictor.")
    station_id: str = ""
    fuel: FuelType = FuelType.PETROL
    bands: list[ForecastBand] = Field(default_factory=list)
    residual_sigma: float = 0.0


# ---------------------------------------------------------------------------------------------
# 4. Recommendation  (Turjo -> Samprity)
# ---------------------------------------------------------------------------------------------

class SignalSeverity(StrEnum):
    INFO = "info"
    WARNING = "warn"
    CRITICAL = "crit"


class RiskSeverity(StrEnum):
    NORMAL = "normal"
    WATCH = "watch"
    WARNING = "warn"
    CRITICAL = "crit"


class StationFuture(_Model):
    station_id: str
    fuel: FuelType
    unmet_liters: float
    min_inventory: float
    final_inventory: float


class AllocationLeg(_Model):
    """One POST /v1/allocations. Legs above route.max_shipment are split before they get here."""
    route_id: str
    source_depot_id: str = ""
    station_id: str = ""
    fuel_type: FuelType = FuelType.PETROL
    quantity: float = Field(gt=0, default=100.0)
    transit_ticks: int = 2

    @model_validator(mode="before")
    @classmethod
    def _map_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "depot_id" in data and "source_depot_id" not in data:
                data["source_depot_id"] = data["depot_id"]
            if "fuel" in data and "fuel_type" not in data:
                data["fuel_type"] = data["fuel"]
            if "quantity_liters" in data and "quantity" not in data:
                data["quantity"] = data["quantity_liters"]
        return data

    @property
    def depot_id(self) -> str:
        return self.source_depot_id

    @property
    def fuel(self) -> FuelType:
        return self.fuel_type

    @property
    def quantity_liters(self) -> float:
        return self.quantity


class Signal(_Model):
    """Why an area is at risk. Shown to operators and handed to the copilot."""
    kind: Literal["demand_anomaly", "depletion", "route_disrupted", "station_outage", "depot_constrained",
                  "supply_delayed", "supply_reduced", "stockout_risk", "stale_data", "other",
                  "demand_spike", "route_disruption", "persistent_demand_drift"] = "other"
    severity: Literal["info", "warn", "crit"] = "info"
    message: str = ""
    station_id: str | None = None
    fuel_type: FuelType | None = None
    value: float | None = None
    id: str = ""
    target_id: str = ""
    threshold: float = 0.0
    detected_at_tick: int = 0

    @model_validator(mode="before")
    @classmethod
    def _map_signal_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "type" in data and "kind" not in data:
                data["kind"] = data["type"]
            if "target_id" in data and "station_id" not in data:
                data["station_id"] = data["target_id"]
        return data

    @property
    def type(self) -> str:
        return self.kind


class RiskItem(_Model):
    station_id: str
    fuel_type: FuelType = FuelType.PETROL
    hours_to_stockout: float | None = Field(None, description="None = no stockout inside the horizon.")
    p_stockout: float = Field(ge=0, le=1, default=0.0)
    has_backup_route: bool = True
    time_to_stockout_ticks: int = 999
    time_to_stockout_hours: float = 99.0
    current_inventory: float = 0.0
    net_inflow_in_transit: float = 0.0
    projected_shortage_liters: float = 0.0
    severity: RiskSeverity = RiskSeverity.NORMAL

    @model_validator(mode="before")
    @classmethod
    def _map_risk_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "fuel" in data and "fuel_type" not in data:
                data["fuel_type"] = data["fuel"]
            if "hours_to_stockout" not in data and "time_to_stockout_hours" in data:
                data["hours_to_stockout"] = data["time_to_stockout_hours"]
        return data

    @property
    def fuel(self) -> FuelType:
        return self.fuel_type


class TwinFuture(_Model):
    """One projected future. Always a projection, never ground truth."""
    candidate_id: str
    label: str = ""
    name: str = ""
    horizon_ticks: int = 24
    network_unmet_liters: float = 0.0
    unmet_by_station: dict[str, float] = Field(default_factory=dict)
    station_outcomes: list[StationFuture] = Field(default_factory=list)
    first_stockout_tick: int | None = None
    service_level: float = Field(ge=0, le=1, default=1.0)
    notes: list[str] | str = Field(default_factory=list, description="e.g. 'Tongi short later'.")
    legs: list[AllocationLeg] = Field(default_factory=list)


class Candidate(_Model):
    id: str = Field(description="'noop', 'greedy-v1', 'lp-v2', ...")
    policy: str
    legs: list[AllocationLeg]


class Recommendation(_Model):
    id: str
    tick: int
    created_at: datetime | str
    mode: Literal["prevention", "containment"] = "prevention"
    candidates: list[Candidate] = Field(default_factory=list)
    selected_candidate_id: str = "lp-v2"
    futures: list[TwinFuture] = Field(default_factory=list, description="Empty if the Twin was unavailable.")
    twin_futures: list[TwinFuture] = Field(default_factory=list)
    risks: list[RiskItem] = Field(default_factory=list)
    signals: list[Signal] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list, description="Constraints that were binding.")
    constraints_applied: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1, default=1.0)
    versions: dict[str, str] = Field(default_factory=dict, description="policy, forecast_model, deployment")
    fallback_used: list[str] = Field(default_factory=list, description="Components that ran on fallback.")
    built_on_stale_data: bool = False
    before_projected_unmet: float = 0.0
    after_projected_unmet: float = 0.0
    projected_unmet_avoided: float = 0.0
    status: str = "PENDING_REVIEW"
    human_review_required: bool = False
    legs: list[AllocationLeg] = Field(default_factory=list)
    policy: str = "lp-v2"
    alternatives: list[str] = Field(default_factory=list)


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
    created_at: datetime | str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    stage: Literal["observed", "predicted", "candidates", "projected", "gated", "approved", "rejected",
                   "submitted", "outcome", "verified"] = "observed"
    versions: dict[str, str] = Field(default_factory=dict)
    mode: Literal["MANUAL", "SUPERVISED", "AUTONOMOUS"] | AutonomyMode | str | None = None
    recommendation: Recommendation | None = None
    gate: dict | None = Field(None, description="{'requires_human': bool, 'reasons': [...]}")
    approval: dict | None = Field(None, description="{'decision': 'approved'|'rejected', 'by': str, 'reason': str}")
    submissions: list[SubmittedAllocation] = Field(default_factory=list)
    outcome: dict | None = None
    twin_check: dict | None = Field(None, description="{'predicted_l', 'actual_l', 'error_l'}")
    observed: dict | None = None
    prediction: dict | None = None
    candidates: list[dict] = Field(default_factory=list)
    twin_projected: dict | None = None
    twin_verified: dict | None = None
    submission: dict | None = None
    timestamp: str | datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def _map_dr_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "timestamp" in data and "created_at" not in data:
                data["created_at"] = data["timestamp"]
            elif "created_at" in data and "timestamp" not in data:
                data["timestamp"] = data["created_at"]
        return data


# ---------------------------------------------------------------------------------------------
# 6. Explanation  (backend -> copilot)
# ---------------------------------------------------------------------------------------------

class ExplainRequest(_Model):
    kind: Literal["decision", "incident", "network_summary", "incident_report"]
    facts: dict = Field(description="Structured facts only. The copilot must not invent numbers.")


class ExplainResponse(_Model):
    text: str
    cited_facts: list[str] = Field(default_factory=list)
    source: Literal["llm", "template"] = "llm"
    confidence: float = 1.0
    llm_model: str = "gpt-4o-mini"
    is_fallback: bool = False

    @model_validator(mode="before")
    @classmethod
    def _map_source_alias(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "source" not in data:
                if data.get("is_fallback") is True:
                    data["source"] = "template"
                else:
                    data["source"] = "llm"
        return data


# ---------------------------------------------------------------------------------------------
# Backend API bodies
# ---------------------------------------------------------------------------------------------

class SubmitAllocationsRequest(_Model):
    decision_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._-]+$")
    legs: list[AllocationLeg] = Field(min_length=1, max_length=50)


class SubmitAllocationsResponse(_Model):
    decision_id: str
    submissions: list[SubmittedAllocation]


class ComponentHealth(_Model):
    name: str
    status: Literal["healthy", "degraded", "down", "unknown"]
    detail: str | None = None


class HealthReport(_Model):
    status: Literal["healthy", "degraded", "down"]
    components: list[ComponentHealth]
    tick: int | None
    snapshot_age_seconds: float | None

# ---------------------------------------------------------------------------------------------
# Convenience Aliases & Properties for Intelligence & Copilot
# ---------------------------------------------------------------------------------------------
class RouteStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    DISRUPTED = "DISRUPTED"

class StationStatus(StrEnum):
    OPEN = "OPEN"
    OUTAGE = "OUTAGE"
    CLOSED = "CLOSED"

class DepotStatus(StrEnum):
    OPEN = "OPEN"
    CONSTRAINED = "CONSTRAINED"
    CLOSED = "CLOSED"
class SupplyStatus(StrEnum):
    SCHEDULED = "SCHEDULED"
    DELAYED = "DELAYED"
    ARRIVED = "ARRIVED"
    CANCELLED = "CANCELLED"
AllocationStatus = Literal["PENDING", "IN_TRANSIT", "ARRIVED", "CANCELLED", "FAILED"]
SimulatorEvent = SimEvent
DetectionSignal = Signal
StockoutRisk = RiskItem

class ForecastBand(_Model):
    tick: int
    mean: float
    p10: float
    p90: float

class AutonomyMode(StrEnum):
    AUTONOMOUS = "AUTONOMOUS"
    SUPERVISED = "SUPERVISED"
    MANUAL = "MANUAL"
