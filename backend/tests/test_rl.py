"""Unit tests for FuelGuard Reinforcement Learning Subsystem."""
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from app.config import Settings
from app.contracts import (
    FUELS,
    AllocationLeg,
    Depot,
    FuelType,
    NetworkSnapshot,
    Route,
    RouteStatus,
    Station,
    StationStatus,
)
from app.decisions.gate import Guardrails
from app.main import create_app
from app.rl.agents.policy import ActorCriticNetwork
from app.rl.agents.ppo_agent import PPOAgent
from app.rl.environment.action import RLActionSpace, validate_action
from app.rl.environment.fuel_env import FuelSupplyEnv, _create_default_snapshot
from app.rl.environment.reward import RewardCalculator
from app.rl.environment.state import RLStateExtractor
from app.rl.inference.predictor import RLPredictor, get_rl_predictor


@pytest.fixture
def mock_snapshot():
    return NetworkSnapshot(
        tick=12,
        depots=[
            Depot(id="Gazipur", dispatch_capacity_per_tick=12000.0, capacity={f: 90000.0 for f in FUELS}, inventory={f: 75000.0 for f in FUELS}),
            Depot(id="Patiya", dispatch_capacity_per_tick=11000.0, capacity={f: 80000.0 for f in FUELS}, inventory={f: 60000.0 for f in FUELS}),
        ],
        stations=[
            Station(id="Tongi", capacity={f: 25000.0 for f in FUELS}, inventory={f: 8000.0 for f in FUELS}),
            Station(id="Airport", capacity={f: 30000.0 for f in FUELS}, inventory={f: 12000.0 for f in FUELS}),
            Station(id="Chittagong Port", capacity={f: 35000.0 for f in FUELS}, inventory={f: 15000.0 for f in FUELS}),
            Station(id="Agrabad", capacity={f: 25000.0 for f in FUELS}, inventory={f: 9000.0 for f in FUELS}),
        ],
        routes=[
            Route(id="Route_1", source_depot_id="Gazipur", destination_station_id="Tongi", transit_ticks=1, max_shipment=5000.0, status="AVAILABLE"),
            Route(id="Route_2", source_depot_id="Gazipur", destination_station_id="Airport", transit_ticks=2, max_shipment=5000.0, status="AVAILABLE"),
            Route(id="Route_3", source_depot_id="Patiya", destination_station_id="Chittagong Port", transit_ticks=1, max_shipment=5000.0, status="AVAILABLE"),
            Route(id="Route_4", source_depot_id="Patiya", destination_station_id="Agrabad", transit_ticks=2, max_shipment=5000.0, status="AVAILABLE"),
        ],
        in_transit=[],
        events=[],
    )


def test_rl_state_extractor(mock_snapshot):
    extractor = RLStateExtractor()
    vec = extractor.extract(mock_snapshot)
    assert len(vec) == extractor.dim
    assert vec.dtype.name == "float32"
    assert all(np_val >= 0.0 for np_val in vec)

    feature_dict = extractor.to_feature_dict(mock_snapshot)
    assert feature_dict["tick"] == 12
    assert feature_dict["depots_count"] == 2
    assert feature_dict["stations_count"] == 4


def test_rl_action_space_and_validation(mock_snapshot):
    action_space = RLActionSpace()
    combos = action_space.get_action_combinations(mock_snapshot)
    assert len(combos) > 1
    assert combos[0]["type"] == "noop"

    # Action 0 -> None (valid no-op)
    noop_leg = action_space.decode_action(0, mock_snapshot)
    assert noop_leg is None
    is_valid, reason, valid_leg = validate_action(noop_leg, mock_snapshot)
    assert is_valid is True
    assert valid_leg is None

    # Valid dispatch action
    leg = action_space.decode_action(1, mock_snapshot)
    assert leg is not None
    is_valid, reason, valid_leg = validate_action(leg, mock_snapshot)
    assert is_valid is True
    assert valid_leg is not None
    assert valid_leg.quantity > 0


def test_rl_action_validation_disrupted_route(mock_snapshot):
    # Disrupt Route_1
    mock_snapshot.route_map["Route_1"].status = RouteStatus.DISRUPTED
    leg = AllocationLeg(
        route_id="Route_1",
        source_depot_id="Gazipur",
        station_id="Tongi",
        fuel_type=FuelType.DIESEL,
        quantity=3000.0,
        transit_ticks=1,
    )
    is_valid, reason, _ = validate_action(leg, mock_snapshot)
    assert is_valid is False
    assert "DISRUPTED" in (reason or "")


def test_rl_action_validation_depot_reserve_floor(mock_snapshot):
    # Deplete Gazipur diesel inventory down to 9,500 L (below 10% of 90,000 = 9,000 reserve)
    # Attempting to dispatch 2,000 L should be rejected
    mock_snapshot.depot_map["Gazipur"].inventory[FuelType.DIESEL] = 9500.0
    leg = AllocationLeg(
        route_id="Route_1",
        source_depot_id="Gazipur",
        station_id="Tongi",
        fuel_type=FuelType.DIESEL,
        quantity=2000.0,
        transit_ticks=1,
    )
    is_valid, reason, _ = validate_action(leg, mock_snapshot)
    assert is_valid is False
    assert "10% reserve" in (reason or "")


def test_rl_action_validation_station_outage(mock_snapshot):
    mock_snapshot.station_map["Tongi"].status = StationStatus.OUTAGE
    leg = AllocationLeg(
        route_id="Route_1",
        source_depot_id="Gazipur",
        station_id="Tongi",
        fuel_type=FuelType.DIESEL,
        quantity=1000.0,
        transit_ticks=1,
    )
    is_valid, reason, _ = validate_action(leg, mock_snapshot)
    assert is_valid is False
    assert "OUTAGE" in (reason or "")


def test_reward_calculator(mock_snapshot):
    calc = RewardCalculator()
    before = mock_snapshot
    after = mock_snapshot

    # Test invalid action penalty
    r_inv, b_inv = calc.compute_reward(
        snapshot_before=before,
        snapshot_after=after,
        leg=None,
        action_valid=False,
    )
    assert r_inv < 0
    assert "invalid_action" in b_inv

    # Test valid satisfaction reward
    r_val, b_val = calc.compute_reward(
        snapshot_before=before,
        snapshot_after=after,
        leg=None,
        action_valid=True,
        unmet_demand_liters=0.0,
        served_demand_liters=5000.0,
    )
    assert r_val > 0
    assert b_val["demand_satisfaction"] > 0
    assert b_val["stockout_penalty"] == 0.0


def test_fuel_supply_env_step_and_transitions():
    env = FuelSupplyEnv(max_ticks=10)
    obs, info = env.reset(seed=42)
    assert len(obs) == env.observation_space.shape[0]
    assert info["tick"] == 0

    # Step action 0 (no-op)
    obs1, r1, term1, trunc1, info1 = env.step(0)
    assert info1["tick"] == 1
    assert info1["action_valid"] is True
    assert not term1

    # Step action 1 (dispatch)
    obs2, r2, term2, trunc2, info2 = env.step(1)
    assert info2["tick"] == 2
    assert "cumulative_reward" in info2


def test_ppo_agent_policy_serialization(tmp_path):
    agent = PPOAgent(input_dim=20, num_actions=5, hidden_dim=32)
    obs = [0.5] * 20
    action, conf = agent.predict(obs)
    assert 0 <= action < 5
    assert 0.0 <= conf <= 1.0

    # Test save and load
    save_file = tmp_path / "test_ppo.pt"
    agent.save(save_file, metadata={"name": "test_agent", "version": "v99"})
    assert save_file.is_file()

    agent2 = PPOAgent(input_dim=20, num_actions=5, hidden_dim=32)
    meta = agent2.load(save_file)
    assert meta["version"] == "v99"

    action2, conf2 = agent2.predict(obs)
    assert action == action2
    assert abs(conf - conf2) < 1e-4


def test_rl_predictor_inference(mock_snapshot):
    predictor = get_rl_predictor()
    res = predictor.predict_recommendation(mock_snapshot)
    assert "model" in res
    assert res["model"]["name"] == "fuel_ppo"
    assert "confidence" in res
    assert "status" in res
    assert res["status"] in ("pending_human_review", "rejected_by_guardrails")
    assert "latency_ms" in res


def test_rl_api_endpoints(mock_snapshot):
    settings = Settings(database_url="", sse_enabled=False)
    app = create_app(settings)

    with TestClient(app) as client:
        # 1. Stats endpoint
        r_stats = client.get("/api/rl/stats")
        assert r_stats.status_code == 200
        stats_data = r_stats.json()
        assert stats_data["model_name"] == "fuel_ppo"
        assert "loaded" in stats_data
        assert "observation_dim" in stats_data

        # 2. Recommend endpoint
        r_rec = client.post("/api/rl/recommend", json={})
        assert r_rec.status_code == 200
        rec_data = r_rec.json()
        assert "model" in rec_data
        assert rec_data["model"]["name"] == "fuel_ppo"
        assert "confidence" in rec_data
        assert "status" in rec_data
        assert rec_data["status"] == "pending_human_review"
