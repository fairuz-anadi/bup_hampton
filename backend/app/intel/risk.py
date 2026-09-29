"""
FuelGuard Risk Engine (backend/app/intel/risk.py)
Projects inventory depletion over a forward horizon, incorporating in-transit
shipments and forecast demand quantiles. Computes time-to-stockout and P(stockout).
"""

from typing import List, Dict, Tuple
from backend.app.contracts import (
    NetworkSnapshot,
    StockoutRisk,
    RiskSeverity,
    FuelType,
    ForecastResponse,
)


class RiskEngine:
    def __init__(self, default_horizon_ticks: int = 24):
        self.default_horizon = default_horizon_ticks

    def evaluate_risks(
        self,
        snapshot: NetworkSnapshot,
        forecasts: Dict[Tuple[str, str], ForecastResponse],
        horizon_ticks: int = 24,
    ) -> List[StockoutRisk]:
        risks: List[StockoutRisk] = []
        current_tick = snapshot.tick

        # Map upcoming in-transit arrivals: (station_id, fuel, arrival_tick) -> quantity
        in_transit_by_tick: Dict[Tuple[str, str, int], float] = {}
        total_in_transit: Dict[Tuple[str, str], float] = {}

        for leg in snapshot.in_transit:
            key_tick = (leg.station_id, leg.fuel.value, leg.arrival_tick)
            in_transit_by_tick[key_tick] = in_transit_by_tick.get(key_tick, 0.0) + leg.quantity
            tot_key = (leg.station_id, leg.fuel.value)
            total_in_transit[tot_key] = total_in_transit.get(tot_key, 0.0) + leg.quantity

        for s_id, station in snapshot.station_map.items():
            for fuel in [FuelType.DIESEL, FuelType.PETROL, FuelType.OCTANE]:
                curr_inv = station.inventory.get(fuel.value, 0.0)
                fc = forecasts.get((s_id, fuel.value))
                if not fc:
                    continue

                sim_inv = curr_inv
                sim_inv_p90 = curr_inv
                stockout_tick = None
                total_unmet = 0.0
                p90_unmet = 0.0

                for band in fc.bands[:horizon_ticks]:
                    tick = band.tick
                    # In-transit fuel arriving at this tick
                    arrivals = in_transit_by_tick.get((s_id, fuel.value, tick), 0.0)
                    sim_inv += arrivals
                    sim_inv_p90 += arrivals

                    # Subtract forecast demand
                    demand_mean = band.mean
                    demand_p90 = band.p90

                    sim_inv -= demand_mean
                    sim_inv_p90 -= demand_p90

                    if sim_inv < 0:
                        if stockout_tick is None:
                            stockout_tick = tick
                        total_unmet += abs(sim_inv)
                        sim_inv = 0.0

                    if sim_inv_p90 < 0:
                        p90_unmet += abs(sim_inv_p90)
                        sim_inv_p90 = 0.0

                time_to_stockout_ticks = (stockout_tick - current_tick) if stockout_tick else (horizon_ticks + 24)
                time_to_stockout_hours = round(time_to_stockout_ticks * 0.25, 1)

                # Probability of stockout estimation
                if stockout_tick and stockout_tick <= current_tick + horizon_ticks:
                    p_stockout = min(0.99, 0.5 + 0.45 * (1.0 - (time_to_stockout_ticks / horizon_ticks)))
                elif p90_unmet > 0:
                    p_stockout = min(0.49, 0.2 + 0.25 * (p90_unmet / max(curr_inv, 1.0)))
                else:
                    p_stockout = max(0.01, 0.05 * (1.0 - (curr_inv / max(station.capacity.get(fuel.value, 1.0), 1.0))))

                # Assign Severity
                if time_to_stockout_hours <= 6.0:
                    severity = RiskSeverity.CRITICAL
                elif time_to_stockout_hours <= 12.0:
                    severity = RiskSeverity.WARNING
                elif time_to_stockout_hours <= 24.0:
                    severity = RiskSeverity.WATCH
                else:
                    severity = RiskSeverity.NORMAL

                net_inflow = total_in_transit.get((s_id, fuel.value), 0.0)

                risks.append(StockoutRisk(
                    station_id=s_id,
                    fuel=fuel,
                    time_to_stockout_ticks=time_to_stockout_ticks,
                    time_to_stockout_hours=time_to_stockout_hours,
                    p_stockout=round(p_stockout, 3),
                    current_inventory=round(curr_inv, 1),
                    net_inflow_in_transit=round(net_inflow, 1),
                    projected_shortage_liters=round(total_unmet, 1),
                    severity=severity,
                ))

        # Sort so most critical risks appear first
        risks.sort(key=lambda r: (r.severity != RiskSeverity.CRITICAL, r.time_to_stockout_ticks, -r.projected_shortage_liters))
        return risks
