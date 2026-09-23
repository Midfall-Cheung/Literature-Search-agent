from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.query_plans import ProviderName
from app.schemas.terms import QueryPurpose


class SearchRunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class QueryExecutionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class UserDecision(StrEnum):
    UNREVIEWED = "unreviewed"
    INCLUDE = "include"
    EXCLUDE = "exclude"


class SearchRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_ids: list[UUID] | None = None
    providers: list[ProviderName] | None = None
    purposes: list[QueryPurpose] = Field(
        default_factory=lambda: [QueryPurpose.BROAD], min_length=1
    )
    max_records_per_query: int | None = Field(default=None, ge=1, le=10000)

    @model_validator(mode="after")
    def validate_selection(self) -> "SearchRunRequest":
        if self.query_ids is not None and not self.query_ids:
            raise ValueError("query_ids 不能为空列表")
        if self.providers is not None and not self.providers:
            raise ValueError("providers 不能为空列表")
        if self.query_ids is not None and self.providers is not None:
            raise ValueError("query_ids 与 providers 不能同时使用")
        return self


class QueryExecution(BaseModel):
    query_id: UUID
    provider: ProviderName
    purpose: QueryPurpose
    status: QueryExecutionStatus
    cursor: str | None = None
    retrieved_count: int = 0
    max_records: int
    estimated_total: int | None = None
    error: str | None = None
    updated_at: datetime


class ProviderRunStats(BaseModel):
    provider: ProviderName
    query_count: int
    completed_queries: int
    failed_queries: int
    raw_record_count: int


class SearchRun(BaseModel):
    run_id: UUID
    project_id: UUID
    query_plan_version: int
    status: SearchRunStatus
    query_executions: list[QueryExecution] = Field(default_factory=list)
    provider_stats: list[ProviderRunStats] = Field(default_factory=list)
    raw_record_count: int = 0
    work_count: int = 0
    error_count: int = 0
    started_at: datetime
    finished_at: datetime | None = None


class SearchPage(BaseModel):
    items: list[dict[str, Any]]
    next_cursor: str | None = None
    estimated_total: int | None = None


class NormalizedWorkCandidate(BaseModel):
    provider: ProviderName
    provider_record_id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    publication_date: date | None = None
    venue: str | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    work_type: str | None = None
    language: str | None = None
    abstract: str | None = None
    author_keywords: list[str] = Field(default_factory=list)
    subjects: list[str] = Field(default_factory=list)
    doi: str | None = None
    pmid: str | None = None
    pmcid: str | None = None
    arxiv_id: str | None = None
    openalex_id: str | None = None
    s2_id: str | None = None
    citation_count: int | None = None
    is_retracted: bool = False
    retraction_source: str | None = None
    is_oa: bool | None = None
    oa_status: str | None = None
    license: str | None = None
    landing_url: str | None = None
    pdf_url: str | None = None
    relevance_score: float = 0.0
    relevance_reason: str = ""


class WorkView(BaseModel):
    work_id: UUID
    project_id: UUID
    title: str
    authors: list[str]
    year: int | None = None
    publication_date: date | None = None
    venue: str | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    work_type: str | None = None
    language: str | None = None
    abstract: str | None = None
    author_keywords: list[str]
    subjects: list[str]
    doi: str | None = None
    pmid: str | None = None
    pmcid: str | None = None
    arxiv_id: str | None = None
    openalex_id: str | None = None
    s2_id: str | None = None
    sources: list[ProviderName]
    matched_query_ids: list[UUID]
    citation_count: int | None = None
    citation_source: ProviderName | None = None
    citation_updated_at: datetime | None = None
    is_retracted: bool
    retraction_source: str | None = None
    relevance_score: float
    relevance_reason: str
    possible_duplicate_of: UUID | None = None
    is_oa: bool | None = None
    oa_status: str | None = None
    license: str | None = None
    landing_url: str | None = None
    pdf_url: str | None = None
    user_decision: UserDecision
    exclude_reason: str | None = None
    notes: str = ""
    tags: list[str]
    first_retrieved_at: datetime
    last_updated_at: datetime


class WorkPage(BaseModel):
    project_id: UUID
    total: int
    offset: int
    limit: int
    items: list[WorkView]
