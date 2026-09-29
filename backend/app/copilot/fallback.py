"""
FuelGuard Deterministic Copilot Fallback (backend/app/copilot/fallback.py)
Provides zero-failure, dependency-free template explanations conforming to §16
whenever LLM/network is unavailable or rate-limited.
"""


from app.contracts import ExplainResponse, NetworkSnapshot, Recommendation


class DeterministicCopilot:
    def explain(
        self,
        recommendation: Recommendation,
        snapshot: NetworkSnapshot,
        query: str = "",
    ) -> ExplainResponse:
        cited_facts: list[str] = []
        rec = recommendation

        # Format allocation legs
        leg_summaries = []
        if rec.legs:
            for leg in rec.legs:
                fact = (
                    f"Route {leg.route_id}: dispatch {leg.quantity_liters:.0f} L of {leg.fuel.value} "
                    f"from {leg.depot_id} to {leg.station_id} (transit: {leg.transit_ticks} ticks)."
                )
                leg_summaries.append(fact)
                cited_facts.append(fact)
        else:
            leg_summaries.append("No allocations recommended for this tick (network stable).")

        # Format risks
        risk_summaries = []
        for r in rec.risks[:3]:
            fact = (
                f"Station {r.station_id} ({r.fuel.value}): inventory={r.current_inventory:.0f} L, "
                f"time to stockout={r.time_to_stockout_hours} h (P={r.p_stockout:.2f})."
            )
            risk_summaries.append(fact)
            cited_facts.append(fact)

        # Format Twin Impact
        avoided_fact = (
            f"Decision Twin projects {rec.projected_unmet_avoided:.0f} L unmet demand avoided "
            f"vs the no-action counterfactual "
            f"({rec.before_projected_unmet:.0f} L -> {rec.after_projected_unmet:.0f} L)."
        )
        cited_facts.append(avoided_fact)

        review_status = "REQUIRED" if rec.human_review_required else "NOT REQUIRED"
        text_lines = [
            f"### Operational Rationale [{rec.policy}]",
            f"Simulation Tick: {rec.tick} | Confidence: {rec.confidence:.2f} | Human Review: {review_status}",
            "",
            "#### Recommended Dispatches",
            *[f"- {line}" for line in leg_summaries],
            "",
            "#### Station Risk Assessment",
            *[f"- {r}" for r in risk_summaries],
            "",
            "#### Decision Twin Network Projection",
            f"- {avoided_fact}",
        ]

        if rec.constraints_applied:
            text_lines.append("")
            text_lines.append("#### Active Constraints")
            for c in rec.constraints_applied:
                text_lines.append(f"- {c}")

        return ExplainResponse(
            text="\n".join(text_lines),
            cited_facts=cited_facts,
            confidence=rec.confidence,
            llm_model="template-fallback",
            is_fallback=True,
        )
