"""Tests for FuelGuard Multi-Agent Decision System (OpenAI Executive + HF Critic + Specialists)."""

from pathlib import Path

import pytest
from app.contracts import (
    AgentAssessment,
    AgentRole,
    AllocationLeg,
    Depot,
    DepotStatus,
    FuelType,
    MultiAgentDecision,
    NetworkSnapshot,
    RiskItem,
    RiskSeverity,
    Route,
    RouteStatus,
    Signal,
    Station,
    StationStatus,
    TwinFuture,
)
from app.intel import IntelligenceService
from app.intel.multiagent import (
    AdversarialCriticAgent,
    DemandForecasterAgent,
    ExecutiveCoordinatorAgent,
    MultiAgentDecisionSystem,
    SafetyAuditorAgent,
    SupplyLogisticsAgent,
)
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


@pytest.fixture
def base_snapshot():
    depots = {
        "depot-gazipur": Depot(
            id="depot-gazipur",
            region="region-dhaka",
            capacity={"DIESEL": 90000, "PETROL": 70000, "OCTANE": 45000},
            inventory={"DIESEL": 60000, "PETROL": 45000, "OCTANE": 26000},
            dispatch_capacity_per_tick=12000,
            status=DepotStatus.OPEN,
        ),
    }
    stations = {
        "station-mirpur": Station(
            id="station-mirpur",
            region="region-dhaka",
            demand_profile="urban_high",
            capacity={"DIESEL": 15000, "PETROL": 14000, "OCTANE": 9000},
            inventory={"DIESEL": 2000, "PETROL": 1500, "OCTANE": 1000},
            demand_multiplier=1.0,
            status=StationStatus.OPEN,
        ),
    }
    routes = {
        "route-gazipur-mirpur": Route(
            id="route-gazipur-mirpur",
            depot_id="depot-gazipur",
            station_id="station-mirpur",
            transit_ticks=2,
            max_shipment=7000,
            status=RouteStatus.AVAILABLE,
        ),
    }
    return NetworkSnapshot(
        tick=10,
        sim_time="Day 1, 02:30",
        depots=depots,
        stations=stations,
        routes=routes,
        in_transit_totals={"station-mirpur": {"PETROL": 500.0}},
    )


@pytest.fixture
def sample_legs():
    return [
        AllocationLeg(
            route_id="route-gazipur-mirpur",
            source_depot_id="depot-gazipur",
            station_id="station-mirpur",
            fuel_type=FuelType.PETROL,
            quantity=3000.0,
        )
    ]


@pytest.fixture
def sample_futures(sample_legs):
    return [
        TwinFuture(candidate_id="noop", network_unmet_liters=6000.0),
        TwinFuture(candidate_id="greedy-v1", network_unmet_liters=3500.0),
        TwinFuture(candidate_id="lp-v2", network_unmet_liters=1000.0, legs=sample_legs),
    ]


def test_demand_forecaster_agent_normal_and_crit(base_snapshot):
    agent = DemandForecasterAgent()
    # Normal
    res = agent.evaluate(base_snapshot, {}, [], [])
    assert res.role == AgentRole.DEMAND_FORECASTER
    assert res.status == "OK"

    # Critical risk (< 4h)
    crit_risk = RiskItem(
        station_id="station-mirpur",
        fuel_type=FuelType.PETROL,
        time_to_stockout_hours=2.5,
        p_stockout=0.9,
        severity=RiskSeverity.CRITICAL,
        projected_shortage_liters=4000.0,
    )
    sig = Signal(kind="demand_spike", severity="crit", message="Severe spike in Dhaka")
    res_crit = agent.evaluate(base_snapshot, {}, [crit_risk], [sig])
    assert res_crit.status == "CRIT"
    assert len(res_crit.concerns) > 0


def test_supply_logistics_agent(base_snapshot, sample_legs):
    agent = SupplyLogisticsAgent()
    res = agent.evaluate(base_snapshot, sample_legs)
    assert res.role == AgentRole.SUPPLY_LOGISTICS
    assert res.status == "OK"

    # Test disrupted route violation
    base_snapshot.route_map["route-gazipur-mirpur"].status = RouteStatus.DISRUPTED
    res_disrupted = agent.evaluate(base_snapshot, sample_legs)
    assert res_disrupted.status == "CRIT"
    assert any("disrupted" in c.lower() for c in res_disrupted.concerns)


def test_safety_auditor_headroom_and_veto(base_snapshot, sample_legs):
    auditor = SafetyAuditorAgent()
    res, req_human = auditor.evaluate(base_snapshot, sample_legs)
    assert res.status == "OK"
    assert not req_human

    # Test tank overflow veto: station capacity is 14000, inv is 1500, in-transit is 500 -> headroom is 12000
    overflow_legs = [
        AllocationLeg(
            route_id="route-gazipur-mirpur",
            source_depot_id="depot-gazipur",
            station_id="station-mirpur",
            fuel_type=FuelType.PETROL,
            quantity=13000.0,
        )
    ]
    res_veto, req_human_veto = auditor.evaluate(base_snapshot, overflow_legs)
    assert res_veto.status == "VETO"
    assert req_human_veto is True
    assert any("overflow" in c.lower() for c in res_veto.concerns)


def test_adversarial_critic_heuristic_fallback(base_snapshot, sample_legs, sample_futures):
    critic = AdversarialCriticAgent(api_key="")  # Empty key forces deterministic fallback
    demand_ass = AgentAssessment(role=AgentRole.DEMAND_FORECASTER, status="OK")
    logistics_ass = AgentAssessment(role=AgentRole.SUPPLY_LOGISTICS, status="OK")

    res, text = critic.critique(base_snapshot, sample_legs, sample_futures, demand_ass, logistics_ass)
    assert res.role == AgentRole.ADVERSARIAL_CRITIC
    assert len(text) > 20
    assert "counterfactual" in text.lower() or "unmet" in text.lower()


def test_executive_coordinator_veto_override(base_snapshot, sample_futures):
    executive = ExecutiveCoordinatorAgent(api_key="")
    demand_ass = AgentAssessment(role=AgentRole.DEMAND_FORECASTER, status="OK")
    logistics_ass = AgentAssessment(role=AgentRole.SUPPLY_LOGISTICS, status="OK")
    safety_veto = AgentAssessment(role=AgentRole.SAFETY_AUDITOR, status="VETO", concerns=["Tank overflow"])
    critic_ass = AgentAssessment(role=AgentRole.ADVERSARIAL_CRITIC, status="OK")

    policy, conf, verdict, req_human = executive.synthesize(
        snapshot=base_snapshot,
        candidate_id="lp-v2",
        twin_futures=sample_futures,
        demand=demand_ass,
        logistics=logistics_ass,
        safety=safety_veto,
        critic=critic_ass,
        critic_text="All clear",
    )
    assert policy == "noop"
    assert req_human is True
    assert "VETO" in verdict


def test_multiagent_system_full_deliberation(base_snapshot, sample_legs, sample_futures):
    system = MultiAgentDecisionSystem()
    decision = system.evaluate_and_deliberate(
        snapshot=base_snapshot,
        forecasts={},
        risks=[],
        signals=[],
        candidate_id="lp-v2",
        candidate_legs=sample_legs,
        twin_futures=sample_futures,
    )
    assert isinstance(decision, MultiAgentDecision)
    assert decision.primary_provider == "openai"
    assert decision.critic_provider == "huggingface"
    assert 0.0 <= decision.consensus_score <= 1.0
    assert len(decision.executive_verdict) > 10
    assert len(decision.critic_review) > 10
    assert AgentRole.DEMAND_FORECASTER.value in decision.agent_assessments
    assert AgentRole.SAFETY_AUDITOR.value in decision.agent_assessments


def test_intelligence_service_multiagent_integration(base_snapshot):
    intel = IntelligenceService()
    rec = intel.evaluate_and_recommend(base_snapshot)
    assert rec.multiagent_decision is not None
    assert rec.multiagent_decision.primary_provider == "openai"
    assert rec.multiagent_decision.selected_policy == rec.selected_candidate_id
    assert rec.confidence > 0.0
