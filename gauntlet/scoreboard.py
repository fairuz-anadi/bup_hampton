"""
FuelGuard Counterfactual Scoreboard (gauntlet/scoreboard.py)
Generates side-by-side counterfactual comparisons and Policy Gauntlet reports.
"""

from typing import Any


class Scoreboard:
    @staticmethod
    def render_markdown_table(eval_result: dict[str, Any]) -> str:
        sc_id = eval_result.get("scenario_id")
        sc_name = eval_result.get("scenario_name")
        passed = eval_result.get("passed", False)
        m = eval_result.get("metrics", {})
        noop = m.get("noop", {})
        base = m.get("baseline", {})
        cand = m.get("candidate", {})
        deltas = eval_result.get("deltas", {})

        verdict = "PASSED" if passed else "REGRESSED"

        sl_row = (
            f"| **Service Level** | {noop.get('service_level', 0.0):.3f} | "
            f"{base.get('service_level', 0.0):.3f} | {cand.get('service_level', 0.0):.3f} | "
            f"{deltas.get('service_level_delta', 0.0):+.3f} pp | >= -0.005 pp |"
        )
        unmet_row = (
            f"| **Unmet Demand (L)** | {noop.get('unmet_demand_liters', 0.0):,.0f} L | "
            f"{base.get('unmet_demand_liters', 0.0):,.0f} L | {cand.get('unmet_demand_liters', 0.0):,.0f} L | "
            f"{deltas.get('unmet_pct_change', 0.0):+.1f}% | <= +2.0% |"
        )
        fail_row = (
            f"| **Allocation Failures** | {noop.get('allocation_failures', 0)} | "
            f"{base.get('allocation_failures', 0)} | {cand.get('allocation_failures', 0)} | "
            f"{deltas.get('failures_delta', 0):+d} | <= Baseline |"
        )
        fb_row = (
            f"| **Fallback Activations**| 0 | 0 | "
            f"{cand.get('fallback_activations', 0)} | — | Report only |"
        )
        impact = max(0.0, noop.get('unmet_demand_liters', 0.0) - cand.get('unmet_demand_liters', 0.0))

        lines = [
            f"### Policy Gauntlet Scoreboard: {sc_name} (`{sc_id}`)",
            f"**Verdict**: **{verdict}**",
            "",
            "| Metric | No-Op | Greedy-v1 (Baseline) | LP-v2 (Candidate) | Delta vs Baseline | Gate Threshold |",
            "|---|---|---|---|---|---|",
            sl_row,
            unmet_row,
            fail_row,
            fb_row,
            "",
            f"> **Projected Impact**: {impact:,.0f} L projected unmet demand avoided vs the no-action counterfactual.",
        ]
        return "\n".join(lines)
