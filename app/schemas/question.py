from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Framework(StrEnum):
    PICO = "pico"
    PECO = "peco"
    SPIDER = "spider"
    CONCEPT_CONTEXT_OUTCOME = "concept_context_outcome"


class ProjectStatus(StrEnum):
    CLARIFYING = "clarifying"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    CONFIRMED = "confirmed"


class FieldName(StrEnum):
    RESEARCH_OBJECTIVE = "research_objective"
    POPULATION_OR_OBJECT = "population_or_object"
    INTERVENTION_OR_EXPOSURE = "intervention_or_exposure"
    COMPARATOR = "comparator"
    OUTCOMES = "outcomes"
    CONTEXT = "context"
    STUDY_TYPES = "study_types"
    DATE_FROM = "date_from"
    DATE_TO = "date_to"
    LANGUAGES = "languages"
    MUST_INCLUDE = "must_include"
    EXCLUDE = "exclude"
    DATABASES = "databases"
    FULLTEXT_REQUIREMENT = "fulltext_requirement"


class ResearchQuestionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    original_question: str = Field(min_length=2)
    framework: Framework = Framework.CONCEPT_CONTEXT_OUTCOME
    research_objective: str | None = None
    population_or_object: str | None = None
    intervention_or_exposure: str | None = None
    comparator: str | None = None
    outcomes: list[str] = Field(default_factory=list)
    context: str | None = None
    study_types: list[str] = Field(default_factory=list)
    date_from: int | None = Field(default=None, ge=1500, le=2100)
    date_to: int | None = Field(default=None, ge=1500, le=2100)
    languages: list[str] = Field(default_factory=list)
    must_include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    databases: list[str] = Field(default_factory=list)
    fulltext_requirement: Literal["abstract_or_fulltext", "open_fulltext_only"] = (
        "abstract_or_fulltext"
    )
    explicitly_unrestricted: list[FieldName] = Field(default_factory=list)
    confirmed: bool = False

    @model_validator(mode="after")
    def validate_date_range(self) -> "ResearchQuestionSpec":
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must be less than or equal to date_to")
        return self


class ClarificationQuestion(BaseModel):
    prompt: str
    target_fields: list[FieldName]
    round_number: int = Field(ge=1, le=6)


class CreateProjectRequest(BaseModel):
    original_question: str = Field(min_length=2, max_length=5000)


class MessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=5000)
    field_updates: dict[str, Any] | None = None


class ConfirmQuestionRequest(BaseModel):
    accepted: bool
    feedback: str | None = Field(default=None, max_length=5000)
    field_updates: dict[str, Any] | None = None

    @model_validator(mode="after")
    def revision_requires_updates(self) -> "ConfirmQuestionRequest":
        if not self.accepted and not self.field_updates:
            raise ValueError("field_updates is required when accepted is false")
        return self


class MessageView(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str
    target_fields: list[str] = Field(default_factory=list)
    created_at: datetime


class ProjectState(BaseModel):
    project_id: UUID
    status: ProjectStatus
    clarification_round: int
    max_rounds: int = 6
    question_spec: ResearchQuestionSpec
    missing_fields: list[FieldName]
    current_question: ClarificationQuestion | None = None
    summary: str | None = None
    assumptions: list[str] = Field(default_factory=list)
    messages: list[MessageView] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

