"""FuelGuard AI Chatbot Service."""
from __future__ import annotations

import os
from typing import Any
from uuid import uuid4

import httpx

from app.chat.contracts import ChatMessage, ChatResponse
from app.chat.prompts import SYSTEM_PROMPT, STARTER_PROMPTS, build_live_context
from app.chat.repo import ChatRepo
from app.contracts import ComponentHealth
from app.obs.logging import log_event


class ChatService:
    def __init__(
        self,
        repo: ChatRepo,
        provider: str = "openai",
        model: str = "gpt-4o-mini",
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float = 12.0,
        max_history_turns: int = 8,
    ):
        self.repo = repo
        self.provider = provider
        self.model = model
        self.api_key = api_key if api_key is not None else os.getenv("OPENAI_API_KEY", "")
        self.base_url = base_url or os.getenv("AI_API_BASE", None)
        self.timeout_seconds = timeout_seconds
        self.max_history_turns = max_history_turns

    def health(self) -> ComponentHealth:
        status = "healthy" if bool(self.api_key) else "degraded"
        detail = (
            f"Provider: {self.provider} ({self.model})"
            if bool(self.api_key)
            else "No AI API key configured (using local domain knowledge base)"
        )
        return ComponentHealth(name="AI Chatbot", status=status, detail=detail)

    async def chat(
        self,
        user_message: str,
        conversation_id: str | None,
        snapshot: Any | None,
        current_view: dict[str, Any] | None,
    ) -> ChatResponse:
        conv_id = conversation_id or uuid4().hex[:16]
        user_msg = ChatMessage(role="user", content=user_message.strip(), source="system")
        self.repo.add_message(conv_id, user_msg)

        live_context = build_live_context(snapshot, current_view)
        history = self.repo.get_context_window(conv_id, max_messages=self.max_history_turns * 2)

        # Attempt LLM completion if key is available
        llm_response: str | None = None
        source: str = "llm"
        if self.api_key:
            try:
                llm_response = await self._call_llm(user_message, history[:-1], live_context)
            except Exception as exc:
                log_event("chat.llm_call_failed", error=str(exc))
                llm_response = None

        if not llm_response:
            source = "knowledge_base"
            llm_response = self._fallback_answer(user_message, snapshot, current_view, live_context)

        assistant_msg = ChatMessage(
            role="assistant",
            content=llm_response,
            source=source,  # type: ignore
        )
        self.repo.add_message(conv_id, assistant_msg)

        prompts = self._suggest_prompts(snapshot, current_view)
        facts_list = [line.strip("- ") for line in live_context.splitlines() if line.startswith("- ")]

        return ChatResponse(
            message=llm_response,
            conversation_id=conv_id,
            source=source,  # type: ignore
            suggested_prompts=prompts,
            live_facts=facts_list[:5],
        )

    async def _call_llm(
        self,
        current_query: str,
        recent_history: list[ChatMessage],
        live_context: str,
    ) -> str:
        messages = [
            {"role": "system", "content": f"{SYSTEM_PROMPT}\n\n{live_context}"},
        ]
        for m in recent_history:
            messages.append({"role": m.role, "content": m.content})
        messages.append({"role": "user", "content": current_query})

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        url = self.base_url or "https://api.openai.com/v1/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 700,
        }

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code != 200:
                raise RuntimeError(f"LLM API returned HTTP {resp.status_code}: {resp.text[:200]}")
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()

    def _fallback_answer(
        self,
        query: str,
        snapshot: Any | None,
        current_view: dict[str, Any] | None,
        live_context: str,
    ) -> str:
        q = query.lower()

        if any(w in q for w in ["tick", "status", "time", "clock", "state"]):
            if snapshot:
                return (
                    f"### Current Simulation State\n\n"
                    f"- **Tick**: `{snapshot.tick}`\n"
                    f"- **Status**: `{snapshot.sim_status}`\n"
                    f"- **Sim Time**: {snapshot.sim_time or 'N/A'}\n"
                    f"- **Circuit Breaker**: `{snapshot.freshness.circuit if snapshot.freshness else 'UNKNOWN'}`\n"
                    f"- **Data Freshness**: {'Stale' if snapshot.freshness and snapshot.freshness.stale else 'Fresh'}\n\n"
                    f"*Tip: Use the Scenario Lab or Demo Pacer to advance or pause the simulation.*"
                )
            return "The simulator is currently initializing or unreachable. No active snapshot is available."

        if any(w in q for w in ["recommendation", "policy", "decision", "allocation"]):
            if current_view and current_view.get("recommendation"):
                rec = current_view["recommendation"]
                gate = current_view.get("gate", {})
                return (
                    f"### Current Recommendation (`{rec.get('id', 'N/A')}`)\n\n"
                    f"- **Active Policy**: `{rec.get('policy', 'greedy-v1')}`\n"
                    f"- **Mode**: `{rec.get('mode', 'prevention')}`\n"
                    f"- **Projected Unmet Demand Avoided**: ~{rec.get('projected_unmet_avoided', 0):.0f} L\n"
                    f"- **Proposed Dispatches**: {len(rec.get('legs', []))} legs\n"
                    f"- **Confidence**: {gate.get('confidence', 0)*100:.1f}%\n"
                    f"- **Human Review Required**: {'Yes' if gate.get('requires_human') else 'No (Auto-executable)'}\n\n"
                    f"You can review, modify, or approve these dispatches in the **Decision Center**."
                )
            return "No recommendation has been generated for the current tick yet."

        if any(w in q for w in ["autonomy", "supervised", "manual", "gate"]):
            return (
                "### FuelGuard Autonomy Architecture\n\n"
                "FuelGuard operates with a tri-state autonomy state machine:\n"
                "1. **`MANUAL`**: Every allocation recommendation requires explicit operator approval before posting to the simulator.\n"
                "2. **`SUPERVISED`**: Recommendations with high confidence (> 85%) and no critical guardrail violations auto-execute. Anomalous or low-confidence decisions require human sign-off.\n"
                "3. **`AUTONOMOUS`**: Continuous automated dispatches, enabled after consecutive healthy ticks with high Twin verification accuracy.\n\n"
                "The **Confidence Gate** continuously evaluates 4 factors: Simulator connectivity, data freshness, event stream health, and Twin forecast variance."
            )

        if any(w in q for w in ["crisis", "playbook", "scenario", "chaos", "spike", "outage"]):
            return (
                "### FuelGuard Crisis Playbooks\n\n"
                "- **Demand Spike**: Rapid regional surge in consumption. Strategy: Ramp dispatches along high-throughput corridors while preserving depot reserves.\n"
                "- **Route Disruption**: Route cut or blocked. Strategy: Divert tankers to alternate routes or apply station rationing.\n"
                "- **Depot Constraint**: Reduced depot throughput. Strategy: Shift delivery load to peer depot.\n"
                "- **Supply Delay**: Inbound refinery delay. Strategy: Stretch inventory horizon and protect stations nearest stockout.\n"
                "- **Station Outage**: Station closed. Strategy: Halt all dispatches immediately to conserve fuel.\n\n"
                "You can trigger and test these scenarios directly in the **Scenario Lab**."
            )

        # General overview fallback
        return (
            f"### FuelGuard Operations Assistant\n\n"
            f"I am FuelGuard's operational copilot. Here is a snapshot of current system telemetry:\n\n"
            f"{live_context}\n\n"
            f"Feel free to ask me about:\n"
            f"- Specific station inventory and stockout projections\n"
            f"- Recommendation details and Decision Twin comparisons\n"
            f"- Autonomy Gate confidence factors\n"
            f"- Crisis management strategies and Chaos Lab events"
        )

    def _suggest_prompts(
        self,
        snapshot: Any | None,
        current_view: dict[str, Any] | None,
    ) -> list[str]:
        suggestions = list(STARTER_PROMPTS[:3])
        if snapshot:
            disrupted = [r.id for r in snapshot.routes if getattr(r, "status", "") == "DISRUPTED"]
            if disrupted:
                suggestions.append(f"What should we do about disrupted route {disrupted[0]}?")
            events = [e for e in snapshot.events if getattr(e, "status", "") == "ACTIVE"]
            if events:
                suggestions.append(f"How is the system responding to {events[0].type}?")
        if len(suggestions) < 4:
            suggestions.append("How does the Autonomy Gate decide when to require human review?")
        return suggestions[:4]
