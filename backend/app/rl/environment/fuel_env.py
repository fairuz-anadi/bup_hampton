"""Gymnasium-Compatible Fuel Supply Environment wrapping the BUP Simulator.

Implements standard gym.Env interface using official FuelGuard NetworkSnapshot contracts,
realistic fuel demand physics, in-transit delivery tracking, and configurable reward weights.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any
import gymnasium as gym
from gymnasium import spaces
import numpy as np

from app.contracts import (
    FUELS,
    AllocationLeg,
    FuelType,
    InTransitLeg,
    NetworkSnapshot,
)
from app.rl.environment.action import RLActionSpace, validate_action
from app.rl.environment.reward import RewardCalculator
from app.rl.environment.state import RLStateExtractor

FIXTURE_PATH = Path(__file__).resolve().parents[4] / "fixtures" / "network_snapshot.json"


def _create_default_snapshot() -> NetworkSnapshot:
    if FIXTURE_PATH.is_file():
        try:
            data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
            return NetworkSnapshot.model_validate(data)
        except Exception:
            pass
    # Minimal fallback snapshot if fixture not found
    from app.contracts import Depot, Route, Station
    return NetworkSnapshot(
        tick=0,
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
            Route(id="Route_1", source_depot_id="Gazipur", destination_station_id="Tongi", transit_ticks=1, max_shipment=5000.0),
            Route(id="Route_2", source_depot_id="Gazipur", destination_station_id="Airport", transit_ticks=2, max_shipment=5000.0),
            Route(id="Route_3", source_depot_id="Patiya", destination_station_id="Chittagong Port", transit_ticks=1, max_shipment=5000.0),
            Route(id="Route_4", source_depot_id="Patiya", destination_station_id="Agrabad", transit_ticks=2, max_shipment=5000.0),
        ],
        in_transit=[],
        events=[],
    )


class FuelSupplyEnv(gym.Env):
    """Reinforcement learning environment for fuel supply chain dispatch."""

    metadata = {"render_modes": ["human"]}

    def __init__(
        self,
        config_path: str | Path | None = None,
        max_ticks: int = 96,
        reward_weights: dict[str, float] | None = None,
    ):
        super().__init__()
        self.max_ticks = max_ticks
        self.state_extractor = RLStateExtractor()
        self.action_space_handler = RLActionSpace()
        self.reward_calculator = RewardCalculator(weights=reward_weights, config_path=config_path)

        # Baseline snapshot template
        self.base_snapshot = _create_default_snapshot()
        self.current_snapshot = copy.deepcopy(self.base_snapshot)

        # Action space: discrete combination index
        n_actions = max(1, self.action_space_handler.num_actions(self.base_snapshot))
        self.action_space = spaces.Discrete(n_actions)

        # Observation space: continuous normalized state vector
        obs_dim = self.state_extractor.dim
        self.observation_space = spaces.Box(
            low=0.0, high=10.0, shape=(obs_dim,), dtype=np.float32
        )

        # Episode tracking
        self.current_tick = 0
        self.cumulative_reward = 0.0
        self.cumulative_unmet = 0.0
        self.cumulative_served = 0.0
        self.allocations_count = 0
        self.invalid_actions_count = 0

    def reset(
        self,
        *,
        seed: int | None = None,
        options: dict[str, Any] | None = None,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        super().reset(seed=seed)
        self.current_snapshot = copy.deepcopy(self.base_snapshot)
        self.current_tick = 0
        self.current_snapshot.tick = 0
        self.cumulative_reward = 0.0
        self.cumulative_unmet = 0.0
        self.cumulative_served = 0.0
        self.allocations_count = 0
        self.invalid_actions_count = 0

        obs = self.state_extractor.extract(self.current_snapshot)
        info = {
            "tick": self.current_tick,
            "status": "reset",
            "cumulative_reward": 0.0,
        }
        return obs, info

    def step(
        self, action: int
    ) -> tuple[np.ndarray, float, bool, bool, dict[str, Any]]:
        snapshot_before = copy.deepcopy(self.current_snapshot)
        self.current_tick += 1
        self.current_snapshot.tick = self.current_tick

        # 1. Decode Action & Validate
        leg = self.action_space_handler.decode_action(action, self.current_snapshot)
        is_valid, rejection_reason, valid_leg = validate_action(leg, self.current_snapshot)

        if not is_valid:
            self.invalid_actions_count += 1

        # 2. Apply Valid Action to Snapshot
        if is_valid and valid_leg is not None:
            self.allocations_count += 1
            depot = self.current_snapshot.depot_map[valid_leg.source_depot_id]
            # Deduct from depot
            depot.inventory[valid_leg.fuel_type] = max(
                0.0, depot.inventory.get(valid_leg.fuel_type, 0.0) - valid_leg.quantity
            )
            # Record dispatch commitment this tick
            self.current_snapshot.dispatched_this_tick[depot.id] = (
                self.current_snapshot.dispatched_this_tick.get(depot.id, 0.0) + valid_leg.quantity
            )
            # Add to in-transit ledger
            arrival_tick = self.current_tick + max(1, valid_leg.transit_ticks)
            self.current_snapshot.in_transit.append(
                InTransitLeg(
                    allocation_id=self.allocations_count,
                    route_id=valid_leg.route_id,
                    source_depot_id=valid_leg.source_depot_id,
                    station_id=valid_leg.station_id,
                    fuel_type=valid_leg.fuel_type,
                    quantity=valid_leg.quantity,
                    status="IN_TRANSIT",
                    expected_arrival_tick=arrival_tick,
                )
            )

        # 3. Simulate Environment Dynamics for This Tick
        # (a) Process In-Transit Deliveries Arriving at Stations
        remaining_in_transit: list[InTransitLeg] = []
        for in_leg in self.current_snapshot.in_transit:
            arr = in_leg.expected_arrival_tick or (self.current_tick + 1)
            if arr <= self.current_tick:
                # Deliver to station
                st = self.current_snapshot.station_map.get(in_leg.station_id)
                if st:
                    cap = st.capacity.get(in_leg.fuel_type, 25000.0)
                    st.inventory[in_leg.fuel_type] = min(
                        cap, st.inventory.get(in_leg.fuel_type, 0.0) + in_leg.quantity
                    )
            else:
                remaining_in_transit.append(in_leg)
        self.current_snapshot.in_transit = remaining_in_transit

        # (b) Simulate Station Fuel Demand (Diurnal Pattern)
        tick_served = 0.0
        tick_unmet = 0.0
        # Phase multiplier based on time of day (morning/evening rush)
        hour_frac = ((self.current_tick * 15) % 1440) / 60.0
        diurnal = 0.8 + 0.4 * np.sin((hour_frac - 6) * np.pi / 12) ** 2

        for station in self.current_snapshot.stations:
            for f in FUELS:
                base_demand = 800.0 * diurnal
                curr_inv = station.inventory.get(f, 0.0)
                served = min(curr_inv, base_demand)
                unmet = max(0.0, base_demand - served)

                station.inventory[f] = max(0.0, curr_inv - served)
                tick_served += served
                tick_unmet += unmet

        self.cumulative_served += tick_served
        self.cumulative_unmet += tick_unmet

        # 4. Compute Multi-Objective Reward
        reward, breakdown = self.reward_calculator.compute_reward(
            snapshot_before=snapshot_before,
            snapshot_after=self.current_snapshot,
            leg=valid_leg,
            action_valid=is_valid,
            unmet_demand_liters=tick_unmet,
            served_demand_liters=tick_served,
            invalid_reason=rejection_reason,
        )
        self.cumulative_reward += reward

        # 5. Check Termination Conditions
        terminated = self.current_tick >= self.max_ticks
        truncated = False

        obs = self.state_extractor.extract(self.current_snapshot)
        info = {
            "tick": self.current_tick,
            "action_valid": is_valid,
            "rejection_reason": rejection_reason,
            "tick_served": tick_served,
            "tick_unmet": tick_unmet,
            "reward_breakdown": breakdown,
            "cumulative_reward": self.cumulative_reward,
            "cumulative_served": self.cumulative_served,
            "cumulative_unmet": self.cumulative_unmet,
            "invalid_actions_count": self.invalid_actions_count,
        }

        return obs, reward, terminated, truncated, info

    def render(self):
        print(f"[FuelSupplyEnv] Tick {self.current_tick}: Reward={self.cumulative_reward:.2f}, Unmet={self.cumulative_unmet:.0f}L")
