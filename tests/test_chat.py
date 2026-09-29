"""Tests for FuelGuard AI Chatbot (contracts, repo, service, and API routes)."""
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from app.chat.contracts import ChatMessage, ChatRequest, ChatResponse
from app.chat.repo import ChatRepo
from app.chat.service import ChatService
from app.contracts import (
    Depot,
    DepotStatus,
    NetworkSnapshot,
    Route,
    RouteStatus,
    Station,
    StationStatus,
)
from app.main import app, create_app
from app.config import get_settings


@pytest.fixture
def mock_snapshot():
    depots = {
        "depot-gazipur": Depot(
            id="depot-gazipur",
            region="region-dhaka",
            capacity={"DIESEL": 90000, "PETROL": 70000, "OCTANE": 45000},
            inventory={"DIESEL": 60000, "PETROL": 45000, "OCTANE": 26000},
            dispatch_capacity_per_tick=12000,
            status=DepotStatus.OPEN,
        ),
    }
    stations = {
        "station-mirpur": Station(
            id="station-mirpur",
            region="region-dhaka",
            demand_profile="urban_high",
            capacity={"DIESEL": 15000, "PETROL": 14000, "OCTANE": 9000},
            inventory={"DIESEL": 1500, "PETROL": 1200, "OCTANE": 800},
            demand_multiplier=1.5,
            status=StationStatus.OPEN,
        ),
    }
    routes = {
        "route-gazipur-mirpur": Route(
            id="route-gazipur-mirpur",
            depot_id="depot-gazipur",
            station_id="station-mirpur",
            transit_ticks=2,
            max_shipment=7000,
            status=RouteStatus.AVAILABLE,
        ),
    }
    return NetworkSnapshot(
        tick=12,
        sim_time="Day 1, 03:00",
        sim_status="RUNNING",
        depots=depots,
        stations=stations,
        routes=routes,
    )


def test_chat_contracts():
    req = ChatRequest(message="Hello FuelGuard")
    assert req.message == "Hello FuelGuard"
    assert req.conversation_id is None

    msg = ChatMessage(role="user", content="Test message")
    assert msg.role == "user"
    assert len(msg.id) > 0
    assert msg.timestamp != ""

    resp = ChatResponse(
        message="AI response",
        conversation_id="conv-123",
        source="llm",
        suggested_prompts=["Q1", "Q2"],
    )
    assert resp.conversation_id == "conv-123"
    assert resp.source == "llm"


def test_chat_repo_memory_and_sliding_window(tmp_path):
    repo = ChatRepo(dsn=None, buffer_path=tmp_path / "buffer.jsonl")
    conv_id = "test-conv"

    # Add 12 messages
    for i in range(12):
        repo.add_message(
            conv_id,
            ChatMessage(role="user" if i % 2 == 0 else "assistant", content=f"msg {i}"),
        )

    history = repo.get_history(conv_id)
    assert len(history) == 12

    # Test sliding window
    window = repo.get_context_window(conv_id, max_messages=6)
    assert len(window) == 6
    assert window[-1].content == "msg 11"

    # Test clear
    repo.clear(conv_id)
    assert repo.get_history(conv_id) == []


@pytest.mark.asyncio
async def test_chat_service_fallback(mock_snapshot, tmp_path):
    repo = ChatRepo(dsn=None, buffer_path=tmp_path / "buffer.jsonl")
    # No API key -> deterministic fallback
    service = ChatService(repo=repo, api_key="")

    res = await service.chat(
        user_message="What is the current simulation tick?",
        conversation_id=None,
        snapshot=mock_snapshot,
        current_view=None,
    )

    assert res.source == "knowledge_base"
    assert "Tick" in res.message
    assert "12" in res.message
    assert len(res.suggested_prompts) > 0


def test_chat_api_end_to_end():
    client = TestClient(app)

    # 1. Test starter prompts
    r_prompts = client.get("/api/chat/prompts")
    assert r_prompts.status_code == 200
    prompts = r_prompts.json().get("prompts", [])
    assert len(prompts) > 0

    # 2. Test empty message validation
    r_empty = client.post("/api/chat", json={"message": "   "})
    assert r_empty.status_code == 422

    # 3. Test sending normal message
    r_send = client.post(
        "/api/chat",
        json={"message": "What is FuelGuard?"},
    )
    assert r_send.status_code == 200
    data = r_send.json()
    assert "message" in data
    assert "conversation_id" in data
    conv_id = data["conversation_id"]

    # 4. Test history retrieval
    r_hist = client.get(f"/api/chat/history/{conv_id}")
    assert r_hist.status_code == 200
    hist = r_hist.json().get("messages", [])
    assert len(hist) == 2  # user + assistant

    # 5. Test conversation continuity
    r_followup = client.post(
        "/api/chat",
        json={"message": "Can you summarize that in 1 sentence?", "conversation_id": conv_id},
    )
    assert r_followup.status_code == 200
    r_hist2 = client.get(f"/api/chat/history/{conv_id}")
    assert len(r_hist2.json().get("messages", [])) == 4

    # 6. Test clear conversation
    r_clear = client.post(f"/api/chat/clear?conversation_id={conv_id}")
    assert r_clear.status_code == 200
    r_hist_cleared = client.get(f"/api/chat/history/{conv_id}")
    assert len(r_hist_cleared.json().get("messages", [])) == 0
