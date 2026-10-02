"""Actor-Critic Neural Network Policy for Fuel Supply Allocation."""
from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.distributions.categorical import Categorical
import numpy as np


class ActorCriticNetwork(nn.Module):
    """PPO Actor-Critic Network with shared representations and dual heads."""

    def __init__(self, input_dim: int, num_actions: int, hidden_dim: int = 128):
        super().__init__()
        self.input_dim = input_dim
        self.num_actions = num_actions
        self.hidden_dim = hidden_dim

        # Shared feature extractor
        self.shared = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.ReLU(),
        )

        # Policy (Actor) Head
        self.actor = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.ReLU(),
            nn.Linear(hidden_dim // 2, num_actions),
        )

        # Value (Critic) Head
        self.critic = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.ReLU(),
            nn.Linear(hidden_dim // 2, 1),
        )

    def forward(self, obs: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        features = self.shared(obs)
        logits = self.actor(features)
        value = self.critic(features)
        return logits, value

    def get_action_and_value(
        self, obs: torch.Tensor, action: torch.Tensor | None = None
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        logits, value = self.forward(obs)
        dist = Categorical(logits=logits)
        if action is None:
            action = dist.sample()
        return action, dist.log_prob(action), dist.entropy(), value.squeeze(-1)

    def predict(
        self, obs: np.ndarray | torch.Tensor, deterministic: bool = True
    ) -> tuple[int, float]:
        """Inference helper: returns chosen action index and policy confidence."""
        self.eval()
        with torch.no_grad():
            if not isinstance(obs, torch.Tensor):
                t_obs = torch.tensor(obs, dtype=torch.float32).unsqueeze(0)
            else:
                t_obs = obs.unsqueeze(0) if obs.dim() == 1 else obs

            logits, _ = self.forward(t_obs)
            probs = F.softmax(logits, dim=-1)

            if deterministic:
                action = int(torch.argmax(probs, dim=-1).item())
            else:
                dist = Categorical(probs=probs)
                action = int(dist.sample().item())

            confidence = float(probs[0, action].item())
            return action, confidence
