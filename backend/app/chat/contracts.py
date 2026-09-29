"""Contracts and Pydantic models for the FuelGuard Chatbot."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    role: Literal["user", "assistant", "system"]
    content: str
    timestamp: str = Field(
        default_factory=lambda: datetime.now(UTC).strftime("%H:%M")
    )
    created_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    source: Literal["llm", "knowledge_base", "system", "error"] = "llm"


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=1000)
    conversation_id: str | None = Field(None, max_length=64)


class ChatResponse(BaseModel):
    message: str
    conversation_id: str
    timestamp: str = Field(
        default_factory=lambda: datetime.now(UTC).strftime("%H:%M")
    )
    source: Literal["llm", "knowledge_base", "system", "error"] = "llm"
    suggested_prompts: list[str] = Field(default_factory=list)
    live_facts: list[str] = Field(default_factory=list)


class ConversationHistoryResponse(BaseModel):
    conversation_id: str
    messages: list[ChatMessage]


class StarterPromptsResponse(BaseModel):
    prompts: list[str]
