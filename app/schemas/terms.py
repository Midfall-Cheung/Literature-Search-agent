from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator


NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class TermSetStatus(StrEnum):
    NOT_GENERATED = "not_generated"
    DRAFT = "draft"
    CONFIRMED = "confirmed"


class TermLanguage(StrEnum):
    ZH = "zh"
    EN = "en"
    OTHER = "other"


class TermType(StrEnum):
    PREFERRED = "preferred"
    SYNONYM = "synonym"
    ABBREVIATION = "abbreviation"
    CONTROLLED_VOCAB = "controlled_vocab"
    SPELLING_VARIANT = "spelling_variant"
    TRANSLATION = "translation"
    EXCLUSION = "exclusion"


class TermSource(StrEnum):
    USER = "user"
    LLM = "llm"
    HEURISTIC = "heuristic"
    MESH = "mesh"
    SEED_PAPER = "seed_paper"
    IMPORTED = "imported"


class FieldHint(StrEnum):
    ALL = "all"
    TITLE = "title"
    ABSTRACT = "abstract"
    KEYWORD = "keyword"
    TITLE_ABSTRACT = "title_abstract"


class TermCandidate(BaseModel):
    """Internal/generated term before persistence."""

    model_config = ConfigDict(extra="forbid")

    concept_id: NonEmptyText
    concept_name: NonEmptyText
    term: NonEmptyText
    language: TermLanguage
    term_type: TermType
    source: TermSource
    field_hint: FieldHint = FieldHint.TITLE_ABSTRACT
    enabled: bool = True
    notes: str = ""


class TermInput(BaseModel):
    """Editable representation accepted from the terms UI/API."""

    model_config = ConfigDict(extra="forbid")

    term_id: UUID | None = None
    concept_id: NonEmptyText
    concept_name: NonEmptyText
    term: NonEmptyText
    language: TermLanguage
    term_type: TermType
    source: TermSource = TermSource.USER
    field_hint: FieldHint = FieldHint.TITLE_ABSTRACT
    enabled: bool = True
    notes: str = ""


class TermRecord(TermInput):
    term_id: UUID
    normalized_term: str
    created_at: datetime
    updated_at: datetime


class ConceptSummary(BaseModel):
    concept_id: str
    concept_name: str
    enabled_term_count: int
    total_term_count: int


class TermTable(BaseModel):
    project_id: UUID
    status: TermSetStatus
    version: int = Field(ge=0)
    concepts: list[ConceptSummary] = Field(default_factory=list)
    terms: list[TermRecord] = Field(default_factory=list)
    confirmed_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class TermSetVersion(BaseModel):
    project_id: UUID
    version: int = Field(ge=1)
    status: TermSetStatus
    snapshot: list[dict[str, Any]]
    created_at: datetime


class TermHistory(BaseModel):
    project_id: UUID
    versions: list[TermSetVersion] = Field(default_factory=list)


class GenerateTermsRequest(BaseModel):
    replace_existing: bool = False


class ReplaceTermsRequest(BaseModel):
    expected_version: int = Field(ge=0)
    terms: list[TermInput] = Field(min_length=1, max_length=2000)

    @field_validator("terms")
    @classmethod
    def unique_term_ids(cls, terms: list[TermInput]) -> list[TermInput]:
        ids = [item.term_id for item in terms if item.term_id is not None]
        if len(ids) != len(set(ids)):
            raise ValueError("term_id 不能重复")
        return terms


class ConfirmTermsRequest(BaseModel):
    expected_version: int = Field(ge=1)


class GeneratedTermCandidate(BaseModel):
    """Restricted schema returned by an LLM; provenance is assigned by code."""

    model_config = ConfigDict(extra="forbid")

    concept_id: NonEmptyText
    term: NonEmptyText
    language: TermLanguage
    term_type: TermType = TermType.SYNONYM
    field_hint: FieldHint = FieldHint.TITLE_ABSTRACT
    notes: str = ""

    @field_validator("term_type")
    @classmethod
    def llm_cannot_claim_controlled_vocabulary(cls, value: TermType) -> TermType:
        if value == TermType.CONTROLLED_VOCAB:
            raise ValueError("LLM 候选词不能标记为受控词表术语")
        return value


class GeneratedTermCandidates(BaseModel):
    terms: list[GeneratedTermCandidate] = Field(default_factory=list, max_length=500)


class QueryPurpose(StrEnum):
    BROAD = "broad"
    FOCUSED = "focused"
    EXACT_PHRASE = "exact_phrase"
    CONTROLLED_VOCAB = "controlled_vocab"


class QueryVariant(BaseModel):
    purpose: QueryPurpose
    canonical_query: str
    included_concept_ids: list[str]
    notes: str


class QueryPreview(BaseModel):
    project_id: UUID
    term_set_version: int
    variants: list[QueryVariant]
