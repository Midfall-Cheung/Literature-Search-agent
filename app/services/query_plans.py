from __future__ import annotations

from app.providers import SearchProvider, default_providers
from app.repositories.projects import ProjectRepository
from app.repositories.query_plans import QueryPlanRepository
from app.repositories.terms import TermRepository
from app.schemas.query_plans import (
    CompileQueryPlanRequest,
    ConfirmQueryPlanRequest,
    ProviderName,
    QueryPlan,
    QueryPlanHistory,
    QueryPlanStatus,
)
from app.schemas.question import ProjectStatus
from app.schemas.terms import TermSetStatus
from app.services.projects import InvalidTransitionError, ProjectNotFoundError


class QueryPlanAlreadyExistsError(ValueError):
    pass


class QueryPlanService:
    def __init__(
        self,
        project_repository: ProjectRepository,
        term_repository: TermRepository,
        query_plan_repository: QueryPlanRepository,
        providers: dict[ProviderName, SearchProvider] | None = None,
    ):
        self.project_repository = project_repository
        self.term_repository = term_repository
        self.repository = query_plan_repository
        self.providers = providers or default_providers()

    async def compile(
        self, project_id: str, request: CompileQueryPlanRequest
    ) -> QueryPlan:
        project, terms = self._confirmed_inputs(project_id)
        existing = self.repository.get(project_id)
        if (
            existing.status != QueryPlanStatus.NOT_COMPILED
            and not request.replace_existing
        ):
            raise QueryPlanAlreadyExistsError(
                "查询计划已经存在；如需重新编译，请设置 replace_existing=true"
            )
        unknown = set(request.providers) - set(self.providers)
        if unknown:
            raise ValueError(
                "不支持的数据源: " + ", ".join(sorted(item.value for item in unknown))
            )
        drafts = []
        for provider_name in request.providers:
            drafts.extend(
                await self.providers[provider_name].compile_query(
                    project.question_spec,
                    terms,
                    page_size=request.page_size,
                    max_records=request.max_records,
                )
            )
        return self.repository.replace(
            project_id,
            drafts,
            term_set_version=terms.version,
            expected_version=existing.version,
        )

    def get(self, project_id: str) -> QueryPlan:
        self._project(project_id)
        return self.repository.get(project_id)

    def history(self, project_id: str) -> QueryPlanHistory:
        self._project(project_id)
        return self.repository.history(project_id)

    def confirm(
        self, project_id: str, request: ConfirmQueryPlanRequest
    ) -> QueryPlan:
        _, terms = self._confirmed_inputs(project_id)
        plan = self.repository.get(project_id)
        if plan.status == QueryPlanStatus.NOT_COMPILED:
            raise InvalidTransitionError("查询计划尚未编译")
        if plan.term_set_version != terms.version:
            raise InvalidTransitionError(
                "查询计划对应的词表版本已过期，请重新编译查询计划"
            )
        return self.repository.confirm(
            project_id, expected_version=request.expected_version
        )

    def _project(self, project_id: str):
        try:
            return self.project_repository.get_state(project_id)
        except (KeyError, ValueError) as exc:
            raise ProjectNotFoundError(project_id) from exc

    def _confirmed_inputs(self, project_id: str):
        project = self._project(project_id)
        if (
            project.status != ProjectStatus.CONFIRMED
            or not project.question_spec.confirmed
        ):
            raise InvalidTransitionError("必须先完成并确认阶段 A 的研究问题")
        terms = self.term_repository.get(project_id)
        if terms.status != TermSetStatus.CONFIRMED:
            raise InvalidTransitionError("必须先完成并确认阶段 B 的检索词表")
        return project, terms
