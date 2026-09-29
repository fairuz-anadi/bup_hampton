"""Operator-facing explanations: templates (always available) + LangGraph copilot on top (read-only).

Kept separate from app/copilot (Turjo's first copilot, which imports via backend.app.*) so the running
backend can import it in every environment, including the Docker image that ships only backend/app.
"""
