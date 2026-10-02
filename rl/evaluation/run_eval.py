"""CLI wrapper to run RL policy evaluation from the repository root."""
import sys
from pathlib import Path

# Add backend to PYTHONPATH
sys.path.append(str(Path(__file__).resolve().parents[2] / "backend"))

from app.rl.training.evaluate import evaluate_rl_agent

if __name__ == "__main__":
    res = evaluate_rl_agent(num_episodes=3)
    print("RL Evaluation Results:", res)
