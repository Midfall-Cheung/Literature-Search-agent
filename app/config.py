from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str
    checkpoint_database_path: Path
    projects_root: Path = Path("./workspace/projects")
    log_level: str = "INFO"
    openalex_api_key: str | None = None
    semantic_scholar_api_key: str | None = None
    crossref_mailto: str | None = None
    http_user_agent: str = "LiteratureSearchAgent/0.5"
    http_timeout_seconds: float = 30.0
    unpaywall_email: str | None = None
    core_api_key: str | None = None
    download_max_file_bytes: int = 100 * 1024 * 1024
    download_connect_timeout_seconds: float = 10.0
    download_total_timeout_seconds: float = 120.0
    download_max_redirects: int = 5
    download_concurrency: int = 3

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.getenv(
                "LITERATURE_DATABASE_URL", "sqlite:///./workspace/literature.db"
            ),
            checkpoint_database_path=Path(
                os.getenv("LITERATURE_CHECKPOINT_DB", "./workspace/checkpoints.db")
            ),
            projects_root=Path(
                os.getenv("LITERATURE_PROJECTS_ROOT", "./workspace/projects")
            ),
            log_level=os.getenv("LITERATURE_LOG_LEVEL", "INFO"),
            openalex_api_key=os.getenv("OPENALEX_API_KEY"),
            semantic_scholar_api_key=os.getenv("SEMANTIC_SCHOLAR_API_KEY"),
            crossref_mailto=os.getenv("CROSSREF_MAILTO"),
            http_user_agent=os.getenv(
                "LITERATURE_HTTP_USER_AGENT", "LiteratureSearchAgent/0.5"
            ),
            http_timeout_seconds=float(
                os.getenv("LITERATURE_HTTP_TIMEOUT_SECONDS", "30")
            ),
            unpaywall_email=os.getenv("UNPAYWALL_EMAIL"),
            core_api_key=os.getenv("CORE_API_KEY"),
            download_max_file_bytes=int(
                os.getenv("LITERATURE_DOWNLOAD_MAX_FILE_BYTES", str(100 * 1024 * 1024))
            ),
            download_connect_timeout_seconds=float(
                os.getenv("LITERATURE_DOWNLOAD_CONNECT_TIMEOUT_SECONDS", "10")
            ),
            download_total_timeout_seconds=float(
                os.getenv("LITERATURE_DOWNLOAD_TOTAL_TIMEOUT_SECONDS", "120")
            ),
            download_max_redirects=int(
                os.getenv("LITERATURE_DOWNLOAD_MAX_REDIRECTS", "5")
            ),
            download_concurrency=int(
                os.getenv("LITERATURE_DOWNLOAD_CONCURRENCY", "3")
            ),
        )

    def ensure_directories(self) -> None:
        self.checkpoint_database_path.parent.mkdir(parents=True, exist_ok=True)
        self.projects_root.mkdir(parents=True, exist_ok=True)
        if self.database_url.startswith("sqlite:///./"):
            Path(self.database_url.removeprefix("sqlite:///./")).parent.mkdir(
                parents=True, exist_ok=True
            )
