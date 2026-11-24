"""Runtime configuration, read from environment variables or a local ``.env`` file."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MONEYLAB_", env_file=".env", extra="ignore")

    home: Path = Path("./var")
    provider: str = "file"
    import_dir: Path = Path("./fixtures/data")
    workers: int = Field(default=0, ge=0)

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


def get_settings() -> Settings:
    return Settings()
