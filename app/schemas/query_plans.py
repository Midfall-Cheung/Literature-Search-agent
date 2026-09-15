from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.schemas.terms import QueryPurpose


class ProviderName(StrEnum):
    OPENALEX = "openalex"
    CROSSREF = "crossref"
    SEMANTIC_SCHOLAR = "semantic_scholar"


class QueryPlanStatus(StrEnum):
    NOT_COMPILED = "not_compiled"
    DRAFT = "draft"
    CONFIRMED = "confirmed"


class ProviderQueryDraft(BaseModel):
    provider: ProviderName
    purpose: QueryPurpose
    canonical_query: str
    provider_query: str
    endpoint: str
    request_params: dict[str, Any]
    filters: dict[str, Any]
    page_size: int = Field(ge=1, le=1000)
    max_records: int = Field(ge=1, le=10000)
    notes: list[str] = Field(default_factory=list)


class ProviderQuery(ProviderQueryDraft):
    query_id: UUID
    project_id: UUID
    plan_version: int = Field(ge=1)
    term_set_version: int = Field(ge=1)
    created_at: datetime


class QueryPlan(BaseModel):
    project_id: UUID
    status: QueryPlanStatus
    version: int = Field(ge=0)
    term_set_version: int = Field(ge=0)
    queries: list[ProviderQuery] = Field(default_factory=list)
    confirmed_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class QueryPlanVersion(BaseModel):
    project_id: UUID
    version: int = Field(ge=1)
    status: QueryPlanStatus
    term_set_version: int = Field(ge=1)
    snapshot: list[dict[str, Any]]
    created_at: datetime


class QueryPlanHistory(BaseModel):
    project_id: UUID
    versions: list[QueryPlanVersion] = Field(default_factory=list)


class CompileQueryPlanRequest(BaseModel):
    providers: list[ProviderName] = Field(
        default_factory=lambda: list(ProviderName), min_length=1
    )
    page_size: int = Field(default=100, ge=1, le=100)
    max_records: int = Field(default=500, ge=1, le=10000)
    replace_existing: bool = False

    @field_validator("providers")
    @classmethod
    def unique_providers(cls, providers: list[ProviderName]) -> list[ProviderName]:
        if len(providers) != len(set(providers)):
            raise ValueError("providers 不能重复")
        return providers


class ConfirmQueryPlanRequest(BaseModel):
    expected_version: int = Field(ge=1)
