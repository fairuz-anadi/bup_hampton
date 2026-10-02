"""FuelGuard RL Training Script.

Trains PPO policy against the Gymnasium FuelSupplyEnv, tracks training losses
and episode returns, and exports saved model checkpoints to backend/app/rl/models/
and top-level rl/models/.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any
import yaml

from app.obs.logging import log_event
from app.rl.agents.ppo_agent import PPOAgent
from app.rl.environment.fuel_env import FuelSupplyEnv

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"


def train_rl_agent(
    config_path: str | Path | None = None,
    total_timesteps: int | None = None,
    save_models: bool = True,
) -> dict[str, Any]:
    cfg_file = Path(config_path or DEFAULT_CONFIG_PATH)
    config: dict[str, Any] = {}
    if cfg_file.is_file():
        with open(cfg_file, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

    agent_cfg = config.get("agent", {})
    train_cfg = config.get("training", {})
    reward_weights = config.get("reward_weights", {})
    paths_cfg = config.get("paths", {})

    timesteps = total_timesteps or train_cfg.get("total_timesteps", 3000)

    # Instantiate Fuel Supply Environment
    env = FuelSupplyEnv(
        config_path=cfg_file,
        reward_weights=reward_weights,
        max_ticks=config.get("environment", {}).get("max_ticks_per_episode", 96),
    )

    input_dim = env.observation_space.shape[0]
    num_actions = env.action_space.n

    # Initialize PPO Agent
    agent = PPOAgent(
        input_dim=input_dim,
        num_actions=num_actions,
        learning_rate=float(agent_cfg.get("learning_rate", 3e-4)),
        gamma=float(agent_cfg.get("gamma", 0.99)),
        gae_lambda=float(agent_cfg.get("gae_lambda", 0.95)),
        clip_range=float(agent_cfg.get("clip_range", 0.2)),
        ent_coef=float(agent_cfg.get("ent_coef", 0.01)),
        vf_coef=float(agent_cfg.get("vf_coef", 0.5)),
        hidden_dim=int(agent_cfg.get("hidden_dim", 128)),
    )

    log_event("rl.training_started", timesteps=timesteps, input_dim=input_dim, num_actions=num_actions)

    results = agent.train_on_env(
        env=env,
        total_timesteps=timesteps,
        rollout_steps=int(agent_cfg.get("rollout_steps", 128)),
        batch_size=int(agent_cfg.get("batch_size", 32)),
        update_epochs=int(agent_cfg.get("update_epochs", 4)),
    )

    if save_models:
        model_name = agent_cfg.get("name", "fuel_ppo")
        version = agent_cfg.get("version", "v1")
        ckpt_name = paths_cfg.get("checkpoint_name", f"{model_name}_{version}.pt")

        metadata = {
            "name": model_name,
            "version": version,
            "timesteps": timesteps,
            "mean_reward": results.get("mean_reward", 0.0),
            "input_dim": input_dim,
            "num_actions": num_actions,
        }

        # 1. Save to backend/app/rl/models/
        backend_model_dir = Path(__file__).resolve().parents[1] / "models"
        agent.save(backend_model_dir / ckpt_name, metadata=metadata)

        # 2. Also save to top-level rl/models/
        root_model_dir = Path(__file__).resolve().parents[4] / "rl" / "models"
        if root_model_dir.exists():
            agent.save(root_model_dir / ckpt_name, metadata=metadata)

        log_event("rl.model_saved", path=str(backend_model_dir / ckpt_name))

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train FuelGuard RL Agent")
    parser.add_argument("--timesteps", type=int, default=3000, help="Total training timesteps")
    parser.add_argument("--config", type=str, default=None, help="Config YAML path")
    args = parser.parse_args()

    res = train_rl_agent(config_path=args.config, total_timesteps=args.timesteps)
    print("\n--- Training Completed ---")
    print(f"Total Steps: {res['total_steps']}")
    print(f"Episodes:    {res['episodes']}")
    print(f"Mean Reward: {res['mean_reward']:.2f}")
    print(f"Policy Loss: {res['final_policy_loss']:.4f}")
