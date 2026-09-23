from __future__ import annotations

import asyncio
import csv
import io
import re
from collections import defaultdict
from uuid import UUID

import httpx

from app.providers.retrievers import RetrievalProvider
from app.repositories.projects import ProjectRepository
from app.repositories.query_plans import QueryPlanRepository
from app.repositories.retrieval import RetrievalRepository
from app.repositories.terms import TermRepository
from app.schemas.query_plans import ProviderName, ProviderQuery, QueryPlanStatus
from app.schemas.retrieval import (
    NormalizedWorkCandidate,
    QueryExecutionStatus,
    SearchRun,
    SearchRunRequest,
    SearchRunStatus,
    WorkPage,
)
from app.schemas.terms import TermRecord, TermSetStatus
from app.services.projects import InvalidTransitionError, ProjectNotFoundError


class SearchRunNotFoundError(KeyError):
    pass


class RetrievalService:
    def __init__(
        self,
        project_repository: ProjectRepository,
        term_repository: TermRepository,
        query_plan_repository: QueryPlanRepository,
        retrieval_repository: RetrievalRepository,
        retrievers: dict[ProviderName, RetrievalProvider],
    ):
        self.project_repository = project_repository
        self.term_repository = term_repository
        self.query_plan_repository = query_plan_repository
        self.repository = retrieval_repository
        self.retrievers = retrievers

    async def start(self, project_id: str, request: SearchRunRequest) -> SearchRun:
        plan = self._confirmed_plan(project_id)
        queries = self._select_queries(plan.queries, request)
        if not queries:
            raise ValueError("没有符合选择条件的查询")
        run_id = self.repository.create_run(
            project_id,
            plan.version,
            queries,
            max_records_per_query=request.max_records_per_query,
        )
        return await self._execute(run_id, queries)

    async def resume(self, project_id: str, run_id: str) -> SearchRun:
        run = self.get_run(project_id, run_id)
        if run.status == SearchRunStatus.COMPLETED:
            return run
        plan = self._confirmed_plan(project_id)
        if plan.version != run.query_plan_version:
            raise InvalidTransitionError(
                "该运行对应的查询计划已不是当前版本，不能安全续跑"
            )
        selected_ids = {item.query_id for item in run.query_executions}
        queries = [item for item in plan.queries if item.query_id in selected_ids]
        self.repository.reopen_run(run_id)
        return await self._execute(run_id, queries)

    def get_run(self, project_id: str, run_id: str) -> SearchRun:
        self._project(project_id)
        try:
            return self.repository.get_run(run_id, project_id)
        except (KeyError, ValueError) as exc:
            raise SearchRunNotFoundError(run_id) from exc

    def works(
        self,
        project_id: str,
        *,
        offset: int,
        limit: int,
        source: ProviderName | None,
        is_oa: bool | None,
    ) -> WorkPage:
        self._project(project_id)
        return self.repository.list_works(
            project_id,
            offset=offset,
            limit=limit,
            source=source.value if source else None,
            is_oa=is_oa,
        )

    def export_csv(self, project_id: str) -> tuple[str, bytes]:
        self._project(project_id)
        works = self.repository.all_works(project_id)
        output = io.StringIO(newline="")
        fieldnames = [
            "work_id",
            "title",
            "authors",
            "year",
            "publication_date",
            "venue",
            "work_type",
            "language",
            "doi",
            "pmid",
            "pmcid",
            "arxiv_id",
            "openalex_id",
            "s2_id",
            "sources",
            "matched_query_ids",
            "citation_count",
            "citation_source",
            "is_retracted",
            "relevance_score",
            "relevance_reason",
            "possible_duplicate_of",
            "is_oa",
            "oa_status",
            "license",
            "landing_url",
            "pdf_url",
            "user_decision",
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for work in works:
            data = work.model_dump(mode="json")
            writer.writerow(
                {
                    key: (
                        " | ".join(str(item) for item in data[key])
                        if isinstance(data.get(key), list)
                        else data.get(key)
                    )
                    for key in fieldnames
                }
            )
        return (
            f"works_{UUID(project_id)}.csv",
            output.getvalue().encode("utf-8-sig"),
        )

    async def _execute(
        self, run_id: str, queries: list[ProviderQuery]
    ) -> SearchRun:
        grouped: dict[ProviderName, list[ProviderQuery]] = defaultdict(list)
        for query in queries:
            state = self.repository.execution_state(run_id, str(query.query_id))
            if state.status != QueryExecutionStatus.COMPLETED.value:
                grouped[query.provider].append(query)
        await asyncio.gather(
            *(self._execute_provider(run_id, items) for items in grouped.values())
        )
        return self.repository.finalize_run(run_id)

    async def _execute_provider(
        self, run_id: str, queries: list[ProviderQuery]
    ) -> None:
        for query in queries:
            try:
                await self._execute_query(run_id, query)
            except Exception as exc:  # provider failures must not cancel other providers
                self.repository.mark_query_failed(
                    run_id, str(query.query_id), _safe_error(exc)
                )

    async def _execute_query(self, run_id: str, query: ProviderQuery) -> None:
        retriever = self.retrievers.get(query.provider)
        if retriever is None:
            raise ValueError(f"没有数据源执行器: {query.provider.value}")
        state = self.repository.execution_state(run_id, str(query.query_id))
        self.repository.mark_query_running(run_id, str(query.query_id))
        terms = self.term_repository.get(str(query.project_id)).terms
        while state.retrieved_count < state.max_records:
            remaining = state.max_records - state.retrieved_count
            page = await retriever.search(
                query,
                cursor=state.cursor,
                limit=min(query.page_size, remaining),
            )
            raw_items = page.items[:remaining]
            candidates = [
                _score_candidate(retriever.normalize(item), terms)
                for item in raw_items
            ]
            reached_limit = state.retrieved_count + len(raw_items) >= state.max_records
            self.repository.ingest_page(
                run_id,
                query,
                raw_items,
                candidates,
                next_cursor=page.next_cursor,
                estimated_total=page.estimated_total,
                reached_limit=reached_limit,
            )
            state = self.repository.execution_state(run_id, str(query.query_id))
            if state.status == QueryExecutionStatus.COMPLETED.value:
                break

    def _confirmed_plan(self, project_id: str):
        self._project(project_id)
        terms = self.term_repository.get(project_id)
        if terms.status != TermSetStatus.CONFIRMED:
            raise InvalidTransitionError("必须先完成并确认阶段 B 的检索词表")
        plan = self.query_plan_repository.get(project_id)
        if plan.status != QueryPlanStatus.CONFIRMED:
            raise InvalidTransitionError("必须先完成并确认阶段 C 的查询计划")
        if plan.term_set_version != terms.version:
            raise InvalidTransitionError("查询计划对应的词表版本已过期")
        return plan

    def _project(self, project_id: str):
        try:
            return self.project_repository.get_state(project_id)
        except (KeyError, ValueError) as exc:
            raise ProjectNotFoundError(project_id) from exc

    @staticmethod
    def _select_queries(
        queries: list[ProviderQuery], request: SearchRunRequest
    ) -> list[ProviderQuery]:
        if request.query_ids is not None:
            requested = set(request.query_ids)
            selected = [item for item in queries if item.query_id in requested]
            if {item.query_id for item in selected} != requested:
                raise ValueError("query_ids 包含不属于当前查询计划的 ID")
            return selected
        providers = set(request.providers) if request.providers else None
        purposes = set(request.purposes)
        return [
            item
            for item in queries
            if (providers is None or item.provider in providers)
            and item.purpose in purposes
        ]


def _score_candidate(
    candidate: NormalizedWorkCandidate, terms: list[TermRecord]
) -> NormalizedWorkCandidate:
    haystack = f"{candidate.title} {candidate.abstract or ''}".casefold()
    enabled = [
        item
        for item in terms
        if item.enabled and item.term_type.value != "exclusion"
    ]
    concepts = {item.concept_id for item in enabled}
    matched_terms = [item for item in enabled if item.normalized_term in haystack]
    matched_concepts = {item.concept_id for item in matched_terms}
    lexical = len(matched_concepts) / len(concepts) if concepts else 0.0
    boundary = 1.0 if candidate.year is not None else 0.5
    metadata_fields = (
        candidate.title,
        candidate.authors,
        candidate.year,
        candidate.venue,
        candidate.doi,
        candidate.abstract,
    )
    completeness = sum(bool(value) for value in metadata_fields) / len(metadata_fields)
    score = round(0.65 * lexical + 0.20 * boundary + 0.15 * completeness, 4)
    reason = (
        f"命中 {len(matched_concepts)}/{len(concepts)} 个概念块、"
        f"{len(matched_terms)} 个启用词；边界信息={boundary:.1f}；"
        f"元数据完整度={completeness:.2f}。未使用语义向量。"
    )
    return candidate.model_copy(
        update={"relevance_score": score, "relevance_reason": reason}
    )


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code} from {exc.request.url.host}"
    if isinstance(exc, httpx.RequestError):
        return f"{type(exc).__name__}: {exc.request.url.host}"
    text = re.sub(r"(?i)(api[_-]?key|token)=[^&\s]+", r"\1=***", str(exc))
    return f"{type(exc).__name__}: {text}"[:4000]
