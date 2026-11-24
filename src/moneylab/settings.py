"""Runtime configuration, read from environment variables or a local ``.env`` file."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MONEYLAB_", env_file=".env", extra="ignore")

    home: Path = Path("./var")
    provider: str = "file"
    import_dir: Path = Path("./fixtures/data")
    workers: int = Field(default=0, ge=0)

    # Provider credential. Read from TIINGO_API_KEY (no MONEYLAB_ prefix) so the same
    # environment secret works for any tool. SecretStr keeps it out of reprs and logs.
    tiingo_api_key: SecretStr | None = Field(default=None, validation_alias="TIINGO_API_KEY")

    @field_validator("tiingo_api_key", mode="before")
    @classmethod
    def _blank_key_is_none(cls, v: object) -> object:
        return None if isinstance(v, str) and not v.strip() else v

    starting_cash: float = 100_000.0
    target_allocation: float = 0.10
    max_positions: int = 10
    cost_bps_per_side: float = 5.0

    @property
    def db_path(self) -> Path:
        return self.home / "moneylab.sqlite"

    @property
    def snapshot_dir(self) -> Path:
        return self.home / "snapshots"

    @property
    def cache_dir(self) -> Path:
        return self.home / "cache"


def get_settings() -> Settings:
    return Settings()
