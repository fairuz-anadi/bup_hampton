"""CLI wrapper to run RL training from the repository root."""
import sys
from pathlib import Path

# Add backend to PYTHONPATH
sys.path.append(str(Path(__file__).resolve().parents[2] / "backend"))

from app.rl.training.train import train_rl_agent

if __name__ == "__main__":
    train_rl_agent(total_timesteps=2000)
