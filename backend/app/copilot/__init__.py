"""FuelGuard Copilot Package."""
from app.copilot.fallback import DeterministicCopilot
from app.copilot.service import CopilotService

__all__ = ["CopilotService", "DeterministicCopilot"]
