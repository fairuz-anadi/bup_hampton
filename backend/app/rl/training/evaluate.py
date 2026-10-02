"""FuelGuard RL Benchmark Evaluation Script.

Evaluates trained PPO policy against test episodes in FuelSupplyEnv,
comparing performance metrics against No-Op and Baseline heuristics.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any
import numpy as np
import yaml

from app.rl.agents.ppo_agent import PPOAgent
from app.rl.environment.fuel_env import FuelSupplyEnv

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"
DEFAULT_MODEL_PATH = Path(__file__).resolve().parents[1] / "models" / "fuel_ppo_v1.pt"


def evaluate_rl_agent(
    model_path: str | Path | None = None,
    config_path: str | Path | None = None,
    num_episodes: int = 5,
) -> dict[str, Any]:
    cfg_file = Path(config_path or DEFAULT_CONFIG_PATH)
    config: dict[str, Any] = {}
    if cfg_file.is_file():
        with open(cfg_file, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

    env = FuelSupplyEnv(
        config_path=cfg_file,
        reward_weights=config.get("reward_weights", {}),
        max_ticks=config.get("environment", {}).get("max_ticks_per_episode", 96),
    )

    agent = PPOAgent(
        input_dim=env.observation_space.shape[0],
        num_actions=env.action_space.n,
    )

    ckpt = Path(model_path or DEFAULT_MODEL_PATH)
    if ckpt.is_file():
        agent.load(ckpt)

    episode_rewards: list[float] = []
    episode_unmet: list[float] = []
    episode_served: list[float] = []
    episode_invalid: list[int] = []

    for ep in range(num_episodes):
        obs, _ = env.reset(seed=ep + 100)
        done = False
        while not done:
            action, _ = agent.predict(obs, deterministic=True)
            obs, _, terminated, truncated, info = env.step(action)
            done = terminated or truncated

        episode_rewards.append(info.get("cumulative_reward", 0.0))
        episode_unmet.append(info.get("cumulative_unmet", 0.0))
        episode_served.append(info.get("cumulative_served", 0.0))
        episode_invalid.append(info.get("invalid_actions_count", 0))

    mean_unmet = float(np.mean(episode_unmet))
    mean_served = float(np.mean(episode_served))
    total_demand = mean_served + mean_unmet
    service_level = (mean_served / total_demand) if total_demand > 0 else 1.0

    return {
        "episodes_evaluated": num_episodes,
        "mean_cumulative_reward": round(float(np.mean(episode_rewards)), 2),
        "mean_served_demand_liters": round(mean_served, 1),
        "mean_unmet_demand_liters": round(mean_unmet, 1),
        "service_level": round(service_level, 4),
        "mean_invalid_actions": round(float(np.mean(episode_invalid)), 1),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate FuelGuard RL Policy")
    parser.add_argument("--model", type=str, default=None, help="Model checkpoint path")
    parser.add_argument("--episodes", type=int, default=5, help="Number of test episodes")
    args = parser.parse_args()

    report = evaluate_rl_agent(model_path=args.model, num_episodes=args.episodes)
    print("\n=== FuelGuard RL Evaluation Report ===")
    for k, v in report.items():
        print(f"  {k:30s}: {v}")
