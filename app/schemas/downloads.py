from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DownloadMode(StrEnum):
    SELECTED = "selected"
    TOP_N_OA = "top_n_oa"
    ALL_OA = "all_oa"


class DownloadRunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class DownloadItemStatus(StrEnum):
    PENDING = "pending"
    DOWNLOADED = "downloaded"
    ALREADY_EXISTS = "already_exists"
    MANUAL_ACCESS_REQUIRED = "manual_access_required"
    FAILED = "failed"


class DownloadAttemptStatus(StrEnum):
    RESOLVING = "resolving"
    NOT_AVAILABLE = "not_available"
    DOWNLOADED = "downloaded"
    INVALID_PDF = "invalid_pdf"
    PERMISSION_DENIED = "permission_denied"
    FAILED = "failed"


class FullTextSource(StrEnum):
    LOCAL = "local"
    METADATA = "metadata"
    UNPAYWALL = "unpaywall"
    EUROPE_PMC = "europe_pmc"
    ARXIV = "arxiv"
    CORE = "core"
    PUBLISHER = "publisher"
    MANUAL = "manual"


class VersionType(StrEnum):
    PUBLISHED = "published"
    ACCEPTED = "accepted"
    PREPRINT = "preprint"
    UNKNOWN = "unknown"


class DownloadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: DownloadMode = DownloadMode.SELECTED
    work_ids: list[UUID] | None = None
    top_n: int | None = Field(default=None, ge=1, le=10000)
    confirm_all_oa: bool = False

    @model_validator(mode="after")
    def validate_policy(self) -> "DownloadRequest":
        if self.mode == DownloadMode.SELECTED:
            if not self.work_ids:
                raise ValueError("selected 模式必须提供 work_ids")
            if self.top_n is not None:
                raise ValueError("selected 模式不能提供 top_n")
        elif self.mode == DownloadMode.TOP_N_OA:
            if self.top_n is None:
                raise ValueError("top_n_oa 模式必须提供 top_n")
            if self.work_ids is not None:
                raise ValueError("top_n_oa 模式不能提供 work_ids")
        else:
            if self.work_ids is not None or self.top_n is not None:
                raise ValueError("all_oa 模式不能提供 work_ids 或 top_n")
        return self


class DownloadEstimate(BaseModel):
    project_id: UUID
    mode: DownloadMode
    eligible_count: int
    already_downloaded_count: int
    estimated_new_files: int
    estimated_bytes: int
    requires_confirmation: bool
    notes: list[str] = Field(default_factory=list)


class ResolvedLocation(BaseModel):
    source: FullTextSource
    url: str
    license: str | None = None
    version_type: VersionType = VersionType.UNKNOWN
    landing_url: str | None = None


class DownloadAttempt(BaseModel):
    attempt_id: UUID
    work_id: UUID
    source: FullTextSource
    status: DownloadAttemptStatus
    source_url: str | None = None
    final_url: str | None = None
    http_status: int | None = None
    error: str | None = None
    started_at: datetime
    finished_at: datetime | None = None


class DownloadedFile(BaseModel):
    file_id: UUID
    sha256: str
    local_path: str
    size_bytes: int
    mime_type: str
    source: FullTextSource
    source_url: str
    final_url: str
    license: str | None = None
    version_type: VersionType
    downloaded_at: datetime


class DownloadItem(BaseModel):
    work_id: UUID
    title: str
    status: DownloadItemStatus
    attempt_count: int
    file: DownloadedFile | None = None
    landing_url: str | None = None
    error: str | None = None


class DownloadRun(BaseModel):
    run_id: UUID
    project_id: UUID
    status: DownloadRunStatus
    mode: DownloadMode
    total_count: int
    downloaded_count: int
    already_exists_count: int
    manual_access_required_count: int
    failed_count: int
    papers_directory: str
    manifest_path: str | None = None
    failed_report_path: str | None = None
    items: list[DownloadItem] = Field(default_factory=list)
    attempts: list[DownloadAttempt] = Field(default_factory=list)
    started_at: datetime
    finished_at: datetime | None = None


class PdfFetchResult(BaseModel):
    http_status: int
    final_url: str
    content_type: str
    size_bytes: int
    sha256: str

