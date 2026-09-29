"""Runtime settings. Every value comes from the environment; see .env.example."""
from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    simulator_url: str = "http://localhost:8000"
    sim_timeout_seconds: float = Field(3.0, gt=0)
    sim_retries: int = Field(2, ge=0, le=5)
    sim_backoff_base_seconds: float = Field(0.2, ge=0)

    # Circuit breaker around the simulator client (blueprint section 08).
    breaker_failure_threshold: int = Field(5, ge=1)
    breaker_window_seconds: float = Field(10.0, gt=0)
    breaker_cooldown_seconds: float = Field(15.0, gt=0)

    # How often the state store refreshes from the simulator, and whether to listen on SSE.
    poll_interval_seconds: float = Field(1.0, gt=0)
    sse_enabled: bool = True
    demand_history_limit: int = Field(600, ge=1, le=2000)

    # Approvals, allocation writes and chaos actions need this key. Empty = writes disabled.
    operator_key: SecretStr = SecretStr("")

    # postgresql://user:pass@host:5432/db. Empty = decision history in memory only.
    database_url: str = ""
    db_buffer_path: str = "/tmp/fuelguard-buffer.jsonl"
    # Turjo's forecaster service, e.g. http://forecaster:8090. Empty = not deployed yet.
    forecaster_url: str = ""
    default_policy: str = "greedy-v1"
    outcome_check_seconds: float = Field(2.0, gt=0)

    log_level: str = "INFO"
    deployment_version: str = "dev"


@lru_cache
def get_settings() -> Settings:
    return Settings()
