from __future__ import annotations

import csv
import io
from uuid import UUID

from app.repositories.projects import ProjectRepository
from app.repositories.terms import TermRepository, TermVersionConflictError
from app.schemas.question import ProjectStatus
from app.schemas.terms import (
    ConfirmTermsRequest,
    GenerateTermsRequest,
    QueryPreview,
    ReplaceTermsRequest,
    TermInput,
    TermSetStatus,
    TermHistory,
    TermSource,
    TermTable,
    TermType,
)
from app.services.projects import InvalidTransitionError, ProjectNotFoundError
from app.services.term_builder import TermBuilder, build_query_preview, normalize_term


class TermSetAlreadyExistsError(ValueError):
    pass


class TermTableService:
    def __init__(
        self,
        project_repository: ProjectRepository,
        term_repository: TermRepository,
        builder: TermBuilder | None = None,
    ):
        self.project_repository = project_repository
        self.term_repository = term_repository
        self.builder = builder or TermBuilder()

    def generate(self, project_id: str, request: GenerateTermsRequest) -> TermTable:
        project = self._confirmed_project(project_id)
        existing = self.term_repository.get(project_id)
        if existing.status != TermSetStatus.NOT_GENERATED and not request.replace_existing:
            raise TermSetAlreadyExistsError(
                "词表已经存在；如需重新生成，请设置 replace_existing=true"
            )
        candidates = self.builder.build(project.question_spec)
        self._validate_unique(candidates)
        return self.term_repository.replace(
            project_id,
            candidates,
            expected_version=existing.version,
            event_type="terms_generated",
        )

    def get(self, project_id: str) -> TermTable:
        self._project(project_id)
        return self.term_repository.get(project_id)

    def history(self, project_id: str) -> TermHistory:
        self._project(project_id)
        return self.term_repository.history(project_id)

    def replace(self, project_id: str, request: ReplaceTermsRequest) -> TermTable:
        self._confirmed_project(project_id)
        self._validate_edit_provenance(request.terms)
        self._validate_unique(request.terms)
        return self.term_repository.replace(
            project_id,
            request.terms,
            expected_version=request.expected_version,
            event_type="terms_edited",
        )

    def confirm(self, project_id: str, request: ConfirmTermsRequest) -> TermTable:
        self._confirmed_project(project_id)
        term_table = self.term_repository.get(project_id)
        if term_table.version != request.expected_version:
            raise TermVersionConflictError(
                f"词表版本冲突：期望 {request.expected_version}，实际 {term_table.version}"
            )
        if term_table.status == TermSetStatus.CONFIRMED:
            return term_table
        positive_concepts = {
            term.concept_id
            for term in term_table.terms
            if term.enabled and term.term_type != TermType.EXCLUSION
        }
        if len(positive_concepts) < 2:
            raise InvalidTransitionError("至少需要两个已启用的非排除概念块才能确认")
        return self.term_repository.confirm(
            project_id, expected_version=request.expected_version
        )

    def preview(self, project_id: str) -> QueryPreview:
        self._confirmed_project(project_id)
        return build_query_preview(self.term_repository.get(project_id))

    def export_csv(self, project_id: str) -> tuple[str, bytes]:
        table = self.get(project_id)
        if table.status == TermSetStatus.NOT_GENERATED:
            raise InvalidTransitionError("词表尚未生成")
        output = io.StringIO(newline="")
        fieldnames = [
            "term_id",
            "concept_id",
            "concept_name",
            "term",
            "normalized_term",
            "language",
            "term_type",
            "source",
            "field_hint",
            "enabled",
            "notes",
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for term in table.terms:
            writer.writerow(
                {
                    "term_id": str(term.term_id),
                    "concept_id": term.concept_id,
                    "concept_name": term.concept_name,
                    "term": term.term,
                    "normalized_term": term.normalized_term,
                    "language": term.language.value,
                    "term_type": term.term_type.value,
                    "source": term.source.value,
                    "field_hint": term.field_hint.value,
                    "enabled": str(term.enabled).lower(),
                    "notes": term.notes,
                }
            )
        filename = f"terms_{UUID(project_id)}_v{table.version}.csv"
        return filename, output.getvalue().encode("utf-8-sig")

    def _project(self, project_id: str):
        try:
            return self.project_repository.get_state(project_id)
        except (KeyError, ValueError) as exc:
            raise ProjectNotFoundError(project_id) from exc

    def _confirmed_project(self, project_id: str):
        project = self._project(project_id)
        if project.status != ProjectStatus.CONFIRMED or not project.question_spec.confirmed:
            raise InvalidTransitionError("必须先完成并确认阶段 A 的研究问题")
        return project

    @staticmethod
    def _validate_unique(terms) -> None:
        seen: set[tuple[str, str]] = set()
        for term in terms:
            key = (term.concept_id, normalize_term(term.term))
            if key in seen:
                raise ValueError(
                    f"概念块 {term.concept_id} 中存在重复词：{term.term}"
                )
            seen.add(key)

    @staticmethod
    def _validate_edit_provenance(terms: list[TermInput]) -> None:
        for term in terms:
            if (
                term.term_type == TermType.CONTROLLED_VOCAB
                and term.source == TermSource.LLM
            ):
                raise ValueError("LLM 生成词不能标记为受控词表术语")
