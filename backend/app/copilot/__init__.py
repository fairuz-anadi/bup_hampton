"""FuelGuard Copilot Package."""
from backend.app.copilot.service import CopilotService
from backend.app.copilot.fallback import DeterministicCopilot

__all__ = ["CopilotService", "DeterministicCopilot"]
