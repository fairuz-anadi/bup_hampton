"""FuelGuard Chat API Endpoints (/api/chat/*)."""
from __future__ import annotations

import time
from collections import defaultdict

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.chat.contracts import (
    ChatRequest,
    ChatResponse,
    ConversationHistoryResponse,
    StarterPromptsResponse,
)

router = APIRouter(prefix="/api/chat", tags=["Chatbot"])

# Sliding window rate limiter: client_ip -> list of timestamps
_RATE_LIMIT_WINDOW = 60.0  # 1 minute
_MAX_REQUESTS_PER_WINDOW = 30
_CLIENT_REQUESTS: dict[str, list[float]] = defaultdict(list)


def _check_rate_limit(request: Request) -> None:
    client_ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    timestamps = _CLIENT_REQUESTS[client_ip]

    # Purge timestamps older than the window
    _CLIENT_REQUESTS[client_ip] = [t for t in timestamps if now - t < _RATE_LIMIT_WINDOW]
    if len(_CLIENT_REQUESTS[client_ip]) >= _MAX_REQUESTS_PER_WINDOW:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many chat requests. Please wait a moment before sending another message.",
        )
    _CLIENT_REQUESTS[client_ip].append(now)


def _services(request: Request):
    svc = getattr(request.app.state, "services", None)
    if svc is None:
        from app.config import get_settings
        from app.main import build_services
        request.app.state.services = build_services(get_settings())
        return request.app.state.services
    return svc


@router.post("", response_model=ChatResponse, summary="Send message to FuelGuard AI Chatbot")
async def send_chat_message(request: Request, body: ChatRequest) -> ChatResponse:
    _check_rate_limit(request)
    svc = _services(request)

    cleaned_message = body.message.strip()
    if not cleaned_message:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Message cannot be empty or purely whitespace.",
        )

    snapshot = svc.store.snapshot
    current_view = svc.engine.current if svc.engine else None

    return await svc.chat.chat(
        user_message=cleaned_message,
        conversation_id=body.conversation_id,
        snapshot=snapshot,
        current_view=current_view,
    )


@router.get("/history/{conversation_id}", response_model=ConversationHistoryResponse, summary="Retrieve conversation history")
def get_chat_history(request: Request, conversation_id: str) -> ConversationHistoryResponse:
    svc = _services(request)
    messages = svc.chat.repo.get_history(conversation_id)
    return ConversationHistoryResponse(
        conversation_id=conversation_id,
        messages=messages,
    )


@router.post("/clear", summary="Clear conversation history")
def clear_chat_history(
    request: Request,
    conversation_id: str = Query(..., min_length=1, max_length=64),
) -> dict[str, str]:
    svc = _services(request)
    svc.chat.repo.clear(conversation_id)
    return {"status": "cleared", "conversation_id": conversation_id}


@router.get("/prompts", response_model=StarterPromptsResponse, summary="Get contextual starter prompts")
def get_starter_prompts(request: Request) -> StarterPromptsResponse:
    svc = _services(request)
    snapshot = svc.store.snapshot
    current_view = svc.engine.current if svc.engine else None
    prompts = svc.chat._suggest_prompts(snapshot, current_view)
    return StarterPromptsResponse(prompts=prompts)
