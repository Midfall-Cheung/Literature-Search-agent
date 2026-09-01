from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str
    checkpoint_database_path: Path
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.getenv(
                "LITERATURE_DATABASE_URL", "sqlite:///./workspace/literature.db"
            ),
            checkpoint_database_path=Path(
                os.getenv("LITERATURE_CHECKPOINT_DB", "./workspace/checkpoints.db")
            ),
            log_level=os.getenv("LITERATURE_LOG_LEVEL", "INFO"),
        )

    def ensure_directories(self) -> None:
        self.checkpoint_database_path.parent.mkdir(parents=True, exist_ok=True)
        if self.database_url.startswith("sqlite:///./"):
            Path(self.database_url.removeprefix("sqlite:///./")).parent.mkdir(
                parents=True, exist_ok=True
            )

