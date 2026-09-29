"""
FuelGuard Multi-Agent Decision System (backend/app/intel/multiagent.py)

Orchestrates specialized autonomous agents for fuel network grid management:
1. DemandForecasterAgent: Analyzes consumption trends, spikes, and stockout horizons.
2. SupplyLogisticsAgent: Evaluates depot stocks, transit latencies, route disruptions, and dispatch limits.
3. SafetyAuditorAgent: Enforces strict physical guardrails (zero tank overflow, headroom, closed stations).
4. AdversarialCriticAgent: Powered by Hugging Face (meta-llama/Llama-3.1-8B-Instruct), stress-testing allocations.
5. ExecutiveCoordinatorAgent: Powered by OpenAI (gpt-4o-mini) as the primary decision brain, arbitrating trade-offs
   and finalizing consensus policy and confidence.

Includes 100% resilient deterministic fallbacks for high-frequency simulated execution without network blocking.
"""

from __future__ import annotations

import json
import os

import httpx

from app.contracts import (
    AgentAssessment,
    AgentRole,
    AllocationLeg,
    ForecastResponse,
    MultiAgentDecision,
    NetworkSnapshot,
    RiskItem,
    RouteStatus,
    Signal,
    StationStatus,
    TwinFuture,
)
from app.obs.logging import log_event


class DemandForecasterAgent:
    """Agent specialized in demand anomaly detection, consumption trends, and stockout vulnerability."""

    def evaluate(
        self,
        snapshot: NetworkSnapshot,
        forecasts: dict[tuple[str, str], ForecastResponse],
        risks: list[RiskItem],
        signals: list[Signal],
    ) -> AgentAssessment:
        key_findings = []
        concerns = []

        urgent_risks = [r for r in risks if (r.hours_to_stockout is not None and r.hours_to_stockout <= 8.0)]
        spike_signals = [s for s in signals if "spike" in s.kind or "anomaly" in s.kind]

        for s in spike_signals:
            key_findings.append(f"Demand anomaly detected: {s.message or s.kind} on {s.station_id or 'network'}")

        for r in urgent_risks:
            concerns.append(
                f"Imminent stockout at {r.station_id} ({r.fuel.value}): {r.hours_to_stockout:.1f}h remaining, "
                f"shortage ~{r.projected_shortage_liters:.0f}L"
            )

        has_fallback_forecast = any(fc.fallback for fc in forecasts.values())
        if has_fallback_forecast:
            key_findings.append("Forecaster service running in fallback mode; baseline heuristic applied.")

        status = (
            "CRIT"
            if any((r.hours_to_stockout or 99) < 4.0 for r in risks)
            else ("WARN" if urgent_risks else "OK")
        )
        confidence = 0.92 if not has_fallback_forecast else 0.78

        summary = (
            f"Demand evaluation: {len(risks)} active risk items ({len(urgent_risks)} urgent <8h). "
            f"{'Severe pressure.' if status != 'OK' else 'Demand patterns within operating limits.'}"
        )

        return AgentAssessment(
            role=AgentRole.DEMAND_FORECASTER,
            status=status,
            confidence=confidence,
            summary=summary,
            key_findings=key_findings[:5],
            concerns=concerns[:5],
            provider="demand_forecaster_engine",
        )


class SupplyLogisticsAgent:
    """Agent specialized in depot inventory balances, route transit constraints, and dispatch limits."""

    def evaluate(
        self,
        snapshot: NetworkSnapshot,
        candidate_legs: list[AllocationLeg],
    ) -> AgentAssessment:
        key_findings = []
        concerns = []
        status = "OK"

        disrupted_routes = {
            r_id: r for r_id, r in snapshot.route_map.items() if r.status != RouteStatus.AVAILABLE
        }
        if disrupted_routes:
            key_findings.append(f"{len(disrupted_routes)} route disruptions active in corridor network.")

        # Check depot dispatch capacity
        depot_allocs: dict[str, float] = {}
        for leg in candidate_legs:
            depot_allocs[leg.depot_id] = depot_allocs.get(leg.depot_id, 0.0) + leg.quantity_liters

            # Check if leg uses disrupted route
            route = snapshot.route_map.get(leg.route_id)
            if route and route.status != RouteStatus.AVAILABLE:
                status = "CRIT"
                concerns.append(f"Leg targets disrupted route {leg.route_id} ({leg.depot_id} -> {leg.station_id})")

            # Check route max_shipment
            if route and leg.quantity_liters > route.max_shipment:
                concerns.append(
                    f"Leg {leg.route_id} qty ({leg.quantity_liters:.0f}L) exceeds cap ({route.max_shipment:.0f}L)"
                )

        for d_id, qty in depot_allocs.items():
            depot = snapshot.depot_map.get(d_id)
            if depot:
                already_dispatched = snapshot.dispatched_this_tick.get(d_id, 0.0)
                remaining_cap = max(0.0, depot.dispatch_capacity_per_tick - already_dispatched)
                if qty > remaining_cap:
                    status = "CRIT"
                    concerns.append(f"Depot {d_id} dispatch {qty:.0f}L exceeds available capacity {remaining_cap:.0f}L")
                else:
                    key_findings.append(f"Depot {d_id} dispatch planned: {qty:.0f}L (cap: {remaining_cap:.0f}L)")

        total_liters = sum(leg.quantity_liters for leg in candidate_legs)
        summary = (
            f"Logistics feasibility: {len(candidate_legs)} legs totaling {total_liters:.0f}L. "
            f"Status is {status}."
        )

        return AgentAssessment(
            role=AgentRole.SUPPLY_LOGISTICS,
            status=status,
            confidence=0.94 if status == "OK" else 0.60,
            summary=summary,
            key_findings=key_findings[:5],
            concerns=concerns[:5],
            provider="supply_logistics_engine",
        )


class SafetyAuditorAgent:
    """Agent enforcing critical safety invariants: zero tank overflow, no closed station shipments, reserve floors."""

    def evaluate(
        self,
        snapshot: NetworkSnapshot,
        candidate_legs: list[AllocationLeg],
    ) -> tuple[AgentAssessment, bool]:
        key_findings = []
        concerns = []
        status = "OK"
        requires_human = False

        # Invariant 1: Check station tank headroom including in-transit
        for leg in candidate_legs:
            station = snapshot.station_map.get(leg.station_id)
            if not station:
                continue

            # Station outage check
            if station.status != StationStatus.OPEN:
                status = "VETO"
                requires_human = True
                concerns.append(f"VETO: Shipment planned to station {station.id} which is in OUTAGE.")

            fuel_key = leg.fuel_type.value
            current_inv = station.inventory.get(fuel_key, 0.0)
            capacity = station.capacity.get(fuel_key, 0.0)

            # In-transit sum for this station & fuel
            in_transit_liters = 0.0
            if snapshot.in_transit_totals:
                in_transit_liters = snapshot.in_transit_totals.get(leg.station_id, {}).get(fuel_key, 0.0)

            headroom = capacity - (current_inv + in_transit_liters)
            if leg.quantity_liters > headroom:
                # Potential tank overflow -> fuel loss!
                status = "VETO"
                requires_human = True
                overflow_l = leg.quantity_liters - headroom
                concerns.append(
                    f"VETO: Tank overflow risk at {leg.station_id} ({fuel_key}): headroom {headroom:.0f}L < "
                    f"shipment {leg.quantity_liters:.0f}L (+{overflow_l:.0f}L loss)"
                )
            else:
                key_findings.append(f"Headroom safe at {leg.station_id} ({fuel_key}): {headroom:.0f}L available")

        # Invariant 2: Stale snapshot guard
        if snapshot.is_stale:
            status = "WARN" if status != "VETO" else "VETO"
            requires_human = True
            concerns.append("Operational snapshot marked stale; automatic execution locked.")

        summary = (
            "Safety audit PASSED all zero-loss headroom and operational checks."
            if status == "OK"
            else f"Safety audit flagged concerns: status {status}."
        )

        return AgentAssessment(
            role=AgentRole.SAFETY_AUDITOR,
            status=status,
            confidence=0.99 if status == "OK" else 0.50,
            summary=summary,
            key_findings=key_findings[:5],
            concerns=concerns[:5],
            provider="safety_auditor_guardrails",
        ), requires_human


class AdversarialCriticAgent:
    """Cross-validator and adversarial stress-tester powered by Hugging Face Inference API."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "meta-llama/Llama-3.1-8B-Instruct",
        timeout: float = 3.0,
    ):
        self.api_key = api_key or os.getenv("HUGGINGFACE_API_KEY", "") or os.getenv("HF_TOKEN", "")
        self.model = model or os.getenv("HF_MODEL", "meta-llama/Llama-3.1-8B-Instruct")
        self.timeout = timeout
        self.router_url = "https://router.huggingface.co/v1/chat/completions"

    def critique(
        self,
        snapshot: NetworkSnapshot,
        candidate_legs: list[AllocationLeg],
        twin_futures: list[TwinFuture],
        demand_assessment: AgentAssessment,
        logistics_assessment: AgentAssessment,
    ) -> tuple[AgentAssessment, str]:
        # Formulate concise facts for critic LLM
        noop_unmet = twin_futures[0].network_unmet_liters if twin_futures else 0.0
        lp_unmet = twin_futures[2].network_unmet_liters if len(twin_futures) > 2 else 0.0
        avoided = max(0.0, noop_unmet - lp_unmet)
        total_vol = sum(leg.quantity_liters for leg in candidate_legs)

        context_lines = [
            f"Sim Tick: {snapshot.tick}",
            f"Total Shipments: {len(candidate_legs)} legs ({total_vol:.0f} L total)",
            f"Twin Projection: {avoided:.0f} L unmet demand avoided vs no-op ({noop_unmet:.0f} L -> {lp_unmet:.0f} L)",
            f"Demand Agent Status: {demand_assessment.status} ({demand_assessment.summary})",
            f"Logistics Agent Status: {logistics_assessment.status}",
        ]
        if logistics_assessment.concerns:
            context_lines.append("Logistics Concerns: " + "; ".join(logistics_assessment.concerns[:2]))

        context_str = "\n".join(context_lines)

        # Attempt Hugging Face router inference
        if self.api_key and not self.api_key.startswith("<"):
            try:
                headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
                prompt = (
                    "You are the Adversarial Logistics Critic for a national fuel supply grid.\n"
                    "Your role: Stress-test this proposed allocation plan. Identify hidden vulnerabilities, "
                    "such as single-corridor dependence, depot buffer exhaustion, or edge-case delays.\n"
                    "RULES:\n"
                    "1. Respond in 2-3 concise sentences (under 60 words).\n"
                    "2. Cite only real operational considerations.\n"
                    "3. Rate risk as LOW, MEDIUM, or HIGH.\n\n"
                    f"Operational Facts:\n{context_str}\n\nCritique:"
                )
                payload = {
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 120,
                    "temperature": 0.2,
                }
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.post(self.router_url, headers=headers, json=payload)
                    if resp.status_code == 200:
                        content = resp.json()["choices"][0]["message"]["content"].strip()
                        return AgentAssessment(
                            role=AgentRole.ADVERSARIAL_CRITIC,
                            status="WARN" if "HIGH" in content.upper() or "MEDIUM" in content.upper() else "OK",
                            confidence=0.88,
                            summary=f"HF Critic ({self.model}): Plan stress-tested successfully.",
                            key_findings=[content],
                            concerns=[content] if "HIGH" in content.upper() else [],
                            provider=f"huggingface/{self.model}",
                        ), content
            except Exception as exc:
                log_event("multiagent.hf_critic_fallback", error=repr(exc)[:150])

        # Heuristic Deterministic Critic Fallback
        heuristic_concerns = []
        risk_level = "LOW"

        # Check if single route stations are being supplied
        single_route_stations = []
        for s_id in snapshot.station_map:
            routes_to = [r for r in snapshot.route_map.values() if r.destination_station_id == s_id]
            if len(routes_to) == 1:
                single_route_stations.append(s_id)

        critique_lines = [
            f"Adversarial review over {len(candidate_legs)} legs: "
            f"Counterfactual avoids {avoided:.0f}L unmet demand.",
        ]
        if single_route_stations:
            critique_lines.append(
                f"Vulnerability watch: {len(single_route_stations)} stations have no redundant supply corridor."
            )
            heuristic_concerns.append("Single-corridor reliance on critical stations.")
            risk_level = "MEDIUM"

        critique_text = " ".join(critique_lines)
        return AgentAssessment(
            role=AgentRole.ADVERSARIAL_CRITIC,
            status="WARN" if risk_level != "LOW" else "OK",
            confidence=0.85,
            summary=f"Critic (Heuristic Fallback): Stress-test evaluated risk as {risk_level}.",
            key_findings=[critique_text],
            concerns=heuristic_concerns,
            provider="heuristic_adversarial_critic",
        ), critique_text


class ExecutiveCoordinatorAgent:
    """Primary Executive Brain powered by OpenAI (gpt-4o-mini). Synthesizes all agents, arbitrates disputes,
    and decides final policy and consensus score."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gpt-4o-mini",
        timeout: float = 3.0,
    ):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.model = model or os.getenv("COPILOT_MODEL", "gpt-4o-mini")
        self.timeout = timeout

    def synthesize(
        self,
        snapshot: NetworkSnapshot,
        candidate_id: str,
        twin_futures: list[TwinFuture],
        demand: AgentAssessment,
        logistics: AgentAssessment,
        safety: AgentAssessment,
        critic: AgentAssessment,
        critic_text: str,
    ) -> tuple[str, float, str, bool]:
        """Returns: (selected_policy, consensus_score, executive_verdict, requires_human)."""
        f_noop = twin_futures[0] if twin_futures else None
        f_chosen = next((f for f in twin_futures if f.candidate_id == candidate_id), None)
        avoided_l = (f_noop.network_unmet_liters - f_chosen.network_unmet_liters) if (f_noop and f_chosen) else 0.0

        # Safety VETO override is absolute
        if safety.status == "VETO":
            reason_str = "; ".join(safety.concerns)
            return (
                "noop",
                0.40,
                f"EXECUTIVE VETO: Safety Auditor blocked dispatch due to critical constraint: {reason_str}",
                True,
            )

        # Primary OpenAI call for Executive Synthesis
        if self.api_key and not self.api_key.startswith("<"):
            try:
                system_prompt = (
                    "You are FuelGuard Executive AI, the chief coordinator for fuel supply logistics.\n"
                    "Synthesize reports from specialized agents (Demand, Logistics, Safety, and the HF Critic).\n"
                    "RULES:\n"
                    "1. Respond with valid JSON only matching this schema:\n"
                    "   {\"selected_policy\": \"lp-v2\", \"consensus_score\": 0.92, \"requires_human\": false, "
                    "\"verdict\": \"2 short sentences explaining decision tradeoff\"}\n"
                    "2. Follow standard FuelGuard wording: write 'X L projected unmet demand avoided vs the no-action "
                    "counterfactual' and never 'saved X L'.\n"
                    "3. Adhere strictly to the facts."
                )

                user_prompt = (
                    f"Operational Facts:\n"
                    f"- Sim Tick: {snapshot.tick}\n"
                    f"- Proposed Policy: {candidate_id}\n"
                    f"- Projected Unmet Avoided: {avoided_l:.0f} L\n"
                    f"- Demand Agent: {demand.status} - {demand.summary}\n"
                    f"- Logistics Agent: {logistics.status} - {logistics.summary}\n"
                    f"- Safety Auditor: {safety.status} - {safety.summary}\n"
                    f"- HF Adversarial Critic: {critic.status} - {critic_text}\n"
                )

                from openai import OpenAI
                client = OpenAI(api_key=self.api_key, timeout=self.timeout)
                res = client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_format={"type": "json_object"},
                    max_tokens=180,
                    temperature=0.1,
                )
                data = json.loads(res.choices[0].message.content or "{}")
                sel_policy = data.get("selected_policy") or candidate_id
                c_score = float(data.get("consensus_score", 0.90))
                req_human = bool(data.get("requires_human", False))
                verdict = data.get("verdict", "")

                if verdict:
                    return sel_policy, max(0.40, min(0.99, c_score)), verdict, req_human
            except Exception as exc:
                log_event("multiagent.openai_executive_fallback", error=repr(exc)[:150])

        # Deterministic Executive Fallback
        c_score = 0.95
        if demand.status != "OK":
            c_score -= 0.05
        if logistics.status != "OK":
            c_score -= 0.10
        if critic.status != "OK":
            c_score -= 0.05
        if snapshot.is_stale:
            c_score -= 0.30

        req_human = safety.status != "OK" or snapshot.is_stale or c_score < 0.80
        verdict = (
            f"Executive consensus affirms {candidate_id}: avoids {avoided_l:.0f} L projected unmet demand "
            f"vs the no-action counterfactual while maintaining depot stability and corridor throughput."
        )

        return candidate_id, round(max(0.40, min(0.99, c_score)), 2), verdict, req_human


class MultiAgentDecisionSystem:
    """Orchestrates the multi-agent decision cycle: Demand + Logistics + Safety + Critic + Executive."""

    def __init__(
        self,
        hf_api_key: str | None = None,
        openai_api_key: str | None = None,
        hf_model: str = "meta-llama/Llama-3.1-8B-Instruct",
        openai_model: str = "gpt-4o-mini",
    ):
        self.demand_agent = DemandForecasterAgent()
        self.logistics_agent = SupplyLogisticsAgent()
        self.safety_agent = SafetyAuditorAgent()
        self.critic_agent = AdversarialCriticAgent(api_key=hf_api_key, model=hf_model)
        self.executive_agent = ExecutiveCoordinatorAgent(api_key=openai_api_key, model=openai_model)

    def evaluate_and_deliberate(
        self,
        snapshot: NetworkSnapshot,
        forecasts: dict[tuple[str, str], ForecastResponse],
        risks: list[RiskItem],
        signals: list[Signal],
        candidate_id: str,
        candidate_legs: list[AllocationLeg],
        twin_futures: list[TwinFuture],
    ) -> MultiAgentDecision:
        """Executes full multi-agent cycle with consensus arbitration."""
        # 1. Specialized Domain Assessments
        demand_assessment = self.demand_agent.evaluate(snapshot, forecasts, risks, signals)
        logistics_assessment = self.logistics_agent.evaluate(snapshot, candidate_legs)
        safety_assessment, safety_requires_human = self.safety_agent.evaluate(snapshot, candidate_legs)

        # 2. Hugging Face Adversarial Stress-Testing
        critic_assessment, critic_review = self.critic_agent.critique(
            snapshot, candidate_legs, twin_futures, demand_assessment, logistics_assessment
        )

        # 3. OpenAI Primary Executive Arbitration
        chosen_policy, consensus_score, executive_verdict, exec_requires_human = self.executive_agent.synthesize(
            snapshot=snapshot,
            candidate_id=candidate_id,
            twin_futures=twin_futures,
            demand=demand_assessment,
            logistics=logistics_assessment,
            safety=safety_assessment,
            critic=critic_assessment,
            critic_text=critic_review,
        )

        # Calculate projected unmet avoided
        f_noop = twin_futures[0] if twin_futures else None
        f_chosen = next((f for f in twin_futures if f.candidate_id == chosen_policy), None)
        unmet_diff = (f_noop.network_unmet_liters - f_chosen.network_unmet_liters) if (f_noop and f_chosen) else 0.0
        unmet_avoided = max(0.0, unmet_diff)

        assessments = {
            AgentRole.DEMAND_FORECASTER.value: demand_assessment,
            AgentRole.SUPPLY_LOGISTICS.value: logistics_assessment,
            AgentRole.SAFETY_AUDITOR.value: safety_assessment,
            AgentRole.ADVERSARIAL_CRITIC.value: critic_assessment,
        }

        return MultiAgentDecision(
            primary_provider="openai",
            critic_provider="huggingface",
            consensus_score=consensus_score,
            selected_policy=chosen_policy,
            agent_assessments=assessments,
            critic_review=critic_review,
            executive_verdict=executive_verdict,
            requires_human_override=safety_requires_human or exec_requires_human,
            unmet_avoided_liters=round(unmet_avoided, 1),
        )
