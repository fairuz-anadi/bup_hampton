"""FuelGuard Copilot Package."""
from backend.app.copilot.fallback import DeterministicCopilot
from backend.app.copilot.service import CopilotService

__all__ = ["CopilotService", "DeterministicCopilot"]
