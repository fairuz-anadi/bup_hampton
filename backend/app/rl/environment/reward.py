"""FuelGuard Multi-Objective RL Reward Function.

Calculates configurable rewards balancing demand satisfaction, low shortage,
transportation cost efficiency, depot reserve preservation, and healthy inventory buffers.
Penalizes stockouts, invalid allocations, capacity violations, and reserve breaches.
"""
from __future__ import annotations

from typing import Any
import yaml
from pathlib import Path

from app.contracts import AllocationLeg, NetworkSnapshot

DEFAULT_REWARD_WEIGHTS: dict[str, float] = {
    "demand_satisfaction_weight": 2.0,
    "stockout_penalty_weight": 3.0,
    "transport_cost_weight": 0.002,
    "reserve_violation_penalty": 5.0,
    "capacity_violation_penalty": 2.0,
    "invalid_action_penalty": 2.5,
    "healthy_inventory_bonus": 0.5,
    "stable_supply_bonus": 0.3,
}


class RewardCalculator:
    """Configurable reward calculation engine driven by YAML or dictionary weights."""

    def __init__(
        self,
        weights: dict[str, float] | None = None,
        config_path: str | Path | None = None,
    ):
        self.weights = dict(DEFAULT_REWARD_WEIGHTS)
        if config_path and Path(config_path).is_file():
            self.load_from_yaml(config_path)
        if weights:
            self.weights.update(weights)

    def load_from_yaml(self, path: str | Path) -> None:
        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
                if data and "reward_weights" in data:
                    self.weights.update(data["reward_weights"])
        except Exception:
            pass

    def compute_reward(
        self,
        snapshot_before: NetworkSnapshot,
        snapshot_after: NetworkSnapshot,
        leg: AllocationLeg | None,
        action_valid: bool,
        unmet_demand_liters: float = 0.0,
        served_demand_liters: float = 0.0,
        invalid_reason: str | None = None,
    ) -> tuple[float, dict[str, float]]:
        """Calculates multi-objective reward and returns total + detailed breakdown."""
        breakdown: dict[str, float] = {}

        # 1. Invalid Action Penalty
        if not action_valid:
            penalty = -self.weights["invalid_action_penalty"]
            breakdown["invalid_action"] = penalty
            return penalty, breakdown

        # 2. Demand Satisfaction & Stockout Penalty
        total_demand = served_demand_liters + unmet_demand_liters
        if total_demand > 0:
            service_level = served_demand_liters / total_demand
            r_sat = self.weights["demand_satisfaction_weight"] * service_level
        else:
            r_sat = self.weights["demand_satisfaction_weight"] * 0.5
        breakdown["demand_satisfaction"] = r_sat

        r_stockout = -self.weights["stockout_penalty_weight"] * (unmet_demand_liters / 1000.0)
        breakdown["stockout_penalty"] = r_stockout

        # 3. Transportation Cost Penalty (proportional to liters * transit_ticks)
        r_transport = 0.0
        if leg and leg.quantity > 0:
            cost = leg.quantity * max(1, leg.transit_ticks) * self.weights["transport_cost_weight"]
            r_transport = -cost
        breakdown["transport_cost"] = r_transport

        # 4. Depot Reserve Constraint Compliance
        r_reserve = 0.0
        depot_reserve_breach = False
        for depot in snapshot_after.depots:
            for fuel, inv in depot.inventory.items():
                cap = depot.capacity.get(fuel, 90000.0) or 90000.0
                reserve_floor = 0.10 * cap
                if inv < reserve_floor:
                    depot_reserve_breach = True
                    break
        if depot_reserve_breach:
            r_reserve = -self.weights["reserve_violation_penalty"]
        breakdown["reserve_penalty"] = r_reserve

        # 5. Healthy Inventory Bonus (encourages maintaining 25% - 85% capacity at stations)
        r_healthy = 0.0
        healthy_count = 0
        total_station_tanks = 0
        for station in snapshot_after.stations:
            for fuel, inv in station.inventory.items():
                cap = station.capacity.get(fuel, 25000.0) or 25000.0
                fill_ratio = inv / max(1.0, cap)
                total_station_tanks += 1
                if 0.25 <= fill_ratio <= 0.85:
                    healthy_count += 1
                elif fill_ratio < 0.10:
                    r_healthy -= 0.2  # Critical low tank penalty

        if total_station_tanks > 0:
            r_healthy += self.weights["healthy_inventory_bonus"] * (healthy_count / total_station_tanks)
        breakdown["healthy_inventory"] = r_healthy

        # Total reward
        total = (
            breakdown.get("demand_satisfaction", 0.0)
            + breakdown.get("stockout_penalty", 0.0)
            + breakdown.get("transport_cost", 0.0)
            + breakdown.get("reserve_penalty", 0.0)
            + breakdown.get("healthy_inventory", 0.0)
        )
        breakdown["total"] = total
        return float(total), breakdown
