"""Proximal Policy Optimization (PPO) Agent for Fuel Supply Allocation.

Supports native PyTorch PPO training with Generalized Advantage Estimation (GAE),
clipped surrogate objective, model checkpoint serialization, and fast production inference.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import numpy as np
import torch
import torch.optim as optim

from app.rl.agents.policy import ActorCriticNetwork


class PPOAgent:
    """Production PPO Agent managing ActorCritic policy training and inference."""

    def __init__(
        self,
        input_dim: int = 73,
        num_actions: int = 97,
        learning_rate: float = 3e-4,
        gamma: float = 0.99,
        gae_lambda: float = 0.95,
        clip_range: float = 0.2,
        ent_coef: float = 0.01,
        vf_coef: float = 0.5,
        max_grad_norm: float = 0.5,
        hidden_dim: int = 128,
        device: str = "cpu",
    ):
        self.input_dim = input_dim
        self.num_actions = num_actions
        self.learning_rate = learning_rate
        self.gamma = gamma
        self.gae_lambda = gae_lambda
        self.clip_range = clip_range
        self.ent_coef = ent_coef
        self.vf_coef = vf_coef
        self.max_grad_norm = max_grad_norm
        self.device = torch.device(device)

        self.network = ActorCriticNetwork(
            input_dim=input_dim, num_actions=num_actions, hidden_dim=hidden_dim
        ).to(self.device)
        self.optimizer = optim.Adam(self.network.parameters(), lr=learning_rate, eps=1e-5)

    def predict(
        self, obs: np.ndarray, deterministic: bool = True
    ) -> tuple[int, float]:
        """Inference mode: returns (action_index, action_confidence)."""
        return self.network.predict(obs, deterministic=deterministic)

    def train_on_env(
        self,
        env: Any,
        total_timesteps: int = 2000,
        rollout_steps: int = 128,
        batch_size: int = 32,
        update_epochs: int = 4,
    ) -> dict[str, Any]:
        """Trains policy against the environment using PPO rollout buffers."""
        self.network.train()
        obs, _ = env.reset()

        episodes = 0
        total_steps = 0
        episode_rewards: list[float] = []
        policy_losses: list[float] = []

        while total_steps < total_timesteps:
            obs_buf: list[np.ndarray] = []
            act_buf: list[int] = []
            logp_buf: list[float] = []
            rew_buf: list[float] = []
            val_buf: list[float] = []
            done_buf: list[bool] = []

            for _ in range(rollout_steps):
                t_obs = torch.tensor(obs, dtype=torch.float32, device=self.device)
                with torch.no_grad():
                    action, log_prob, _, value = self.network.get_action_and_value(t_obs)

                next_obs, reward, terminated, truncated, info = env.step(action.item())
                done = terminated or truncated

                obs_buf.append(obs)
                act_buf.append(action.item())
                logp_buf.append(log_prob.item())
                rew_buf.append(reward)
                val_buf.append(value.item())
                done_buf.append(done)

                obs = next_obs
                total_steps += 1

                if done:
                    episode_rewards.append(info.get("cumulative_reward", 0.0))
                    episodes += 1
                    obs, _ = env.reset()
                    break

            if not obs_buf:
                continue

            # Compute GAE returns and advantages
            with torch.no_grad():
                next_val = (
                    0.0
                    if done_buf[-1]
                    else self.network.get_action_and_value(
                        torch.tensor(obs, dtype=torch.float32, device=self.device)
                    )[3].item()
                )

            advantages = np.zeros(len(rew_buf), dtype=np.float32)
            last_gae = 0.0
            for t in reversed(range(len(rew_buf))):
                next_value = next_val if t == len(rew_buf) - 1 else val_buf[t + 1]
                non_terminal = 0.0 if done_buf[t] else 1.0
                delta = rew_buf[t] + self.gamma * next_value * non_terminal - val_buf[t]
                last_gae = delta + self.gamma * self.gae_lambda * non_terminal * last_gae
                advantages[t] = last_gae

            returns = advantages + np.array(val_buf, dtype=np.float32)
            # Normalize advantages
            adv_mean, adv_std = np.mean(advantages), np.std(advantages) + 1e-8
            norm_advantages = (advantages - adv_mean) / adv_std

            # Convert to tensors
            t_obs_b = torch.tensor(np.array(obs_buf), dtype=torch.float32, device=self.device)
            t_act_b = torch.tensor(act_buf, dtype=torch.long, device=self.device)
            t_old_logp = torch.tensor(logp_buf, dtype=torch.float32, device=self.device)
            t_adv = torch.tensor(norm_advantages, dtype=torch.float32, device=self.device)
            t_ret = torch.tensor(returns, dtype=torch.float32, device=self.device)

            # Mini-batch PPO Updates
            indices = np.arange(len(obs_buf))
            for _ in range(update_epochs):
                np.random.shuffle(indices)
                for start_idx in range(0, len(obs_buf), batch_size):
                    batch_idx = indices[start_idx : start_idx + batch_size]
                    if len(batch_idx) < 4:
                        continue

                    _, new_logp, entropy, new_val = self.network.get_action_and_value(
                        t_obs_b[batch_idx], t_act_b[batch_idx]
                    )

                    ratio = torch.exp(new_logp - t_old_logp[batch_idx])
                    surr1 = ratio * t_adv[batch_idx]
                    surr2 = torch.clamp(ratio, 1.0 - self.clip_range, 1.0 + self.clip_range) * t_adv[batch_idx]
                    policy_loss = -torch.min(surr1, surr2).mean()

                    value_loss = 0.5 * ((new_val - t_ret[batch_idx]) ** 2).mean()
                    loss = policy_loss + self.vf_coef * value_loss - self.ent_coef * entropy.mean()

                    self.optimizer.zero_grad()
                    loss.backward()
                    torch.nn.utils.clip_grad_norm_(self.network.parameters(), self.max_grad_norm)
                    self.optimizer.step()

                    policy_losses.append(policy_loss.item())

        return {
            "total_steps": total_steps,
            "episodes": episodes,
            "mean_reward": float(np.mean(episode_rewards)) if episode_rewards else 0.0,
            "final_policy_loss": float(np.mean(policy_losses[-10:])) if policy_losses else 0.0,
        }

    def save(self, file_path: str | Path, metadata: dict[str, Any] | None = None) -> None:
        target = Path(file_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        checkpoint = {
            "input_dim": self.input_dim,
            "num_actions": self.num_actions,
            "state_dict": self.network.state_dict(),
            "metadata": metadata or {},
        }
        torch.save(checkpoint, str(target))

        meta_path = target.parent / "model_metadata.json"
        meta_content = {
            "name": str((metadata or {}).get("name", "fuel_ppo")),
            "version": str((metadata or {}).get("version", "v1")),
            "input_dim": int(self.input_dim),
            "num_actions": int(self.num_actions),
            "file": target.name,
        }
        meta_path.write_text(json.dumps(meta_content, indent=2, default=str), encoding="utf-8")

    def load(self, file_path: str | Path) -> dict[str, Any]:
        target = Path(file_path)
        if not target.is_file():
            raise FileNotFoundError(f"Model checkpoint not found: {target}")

        checkpoint = torch.load(str(target), map_location=self.device, weights_only=False)
        self.input_dim = checkpoint.get("input_dim", self.input_dim)
        self.num_actions = checkpoint.get("num_actions", self.num_actions)

        # Re-initialize network if dimensions changed
        if (
            self.network.input_dim != self.input_dim
            or self.network.num_actions != self.num_actions
        ):
            self.network = ActorCriticNetwork(
                input_dim=self.input_dim, num_actions=self.num_actions
            ).to(self.device)

        self.network.load_state_dict(checkpoint["state_dict"])
        self.network.eval()
        return checkpoint.get("metadata", {})
