from __future__ import annotations

import difflib
import hashlib
import json
import re
import unicodedata
from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
    or_,
    select,
)
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker

from app.repositories.projects import AuditEventRow, Base, utcnow
from app.schemas.query_plans import ProviderQuery
from app.schemas.retrieval import (
    NormalizedWorkCandidate,
    ProviderRunStats,
    QueryExecution,
    QueryExecutionStatus,
    SearchRun,
    SearchRunStatus,
    UserDecision,
    WorkPage,
    WorkView,
)


class RetrievalRunRow(Base):
    __tablename__ = "retrieval_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    query_plan_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class QueryExecutionRow(Base):
    __tablename__ = "query_executions"
    __table_args__ = (
        UniqueConstraint("run_id", "query_id", name="uq_run_query_execution"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("retrieval_runs.id", ondelete="CASCADE"), index=True
    )
    query_id: Mapped[str] = mapped_column(String(36), index=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    purpose: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), index=True)
    cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    retrieved_count: Mapped[int] = mapped_column(Integer, default=0)
    max_records: Mapped[int] = mapped_column(Integer)
    estimated_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class RawRecordRow(Base):
    __tablename__ = "raw_records"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "query_id", "provider_record_id", name="uq_raw_run_query_record"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("retrieval_runs.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    query_id: Mapped[str] = mapped_column(String(36), index=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    provider_record_id: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    response_hash: Mapped[str] = mapped_column(String(64))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorkRow(Base):
    __tablename__ = "works"
    __table_args__ = (
        UniqueConstraint("project_id", "doi", name="uq_project_work_doi"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(Text)
    normalized_title: Mapped[str] = mapped_column(Text, index=True)
    authors: Mapped[list[str]] = mapped_column(JSON, default=list)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    publication_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    venue: Mapped[str | None] = mapped_column(Text, nullable=True)
    volume: Mapped[str | None] = mapped_column(String(64), nullable=True)
    issue: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pages: Mapped[str | None] = mapped_column(String(64), nullable=True)
    work_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    language: Mapped[str | None] = mapped_column(String(32), nullable=True)
    abstract: Mapped[str | None] = mapped_column(Text, nullable=True)
    author_keywords: Mapped[list[str]] = mapped_column(JSON, default=list)
    subjects: Mapped[list[str]] = mapped_column(JSON, default=list)
    doi: Mapped[str | None] = mapped_column(Text, nullable=True)
    pmid: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    pmcid: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    arxiv_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    openalex_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    s2_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    citation_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    citation_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    citation_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_retracted: Mapped[bool] = mapped_column(Boolean, default=False)
    retraction_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    relevance_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    relevance_reason: Mapped[str] = mapped_column(Text, default="")
    possible_duplicate_of: Mapped[str | None] = mapped_column(
        ForeignKey("works.id"), nullable=True
    )
    is_oa: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    oa_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    license: Mapped[str | None] = mapped_column(Text, nullable=True)
    landing_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    pdf_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_decision: Mapped[str] = mapped_column(
        String(32), default=UserDecision.UNREVIEWED.value
    )
    exclude_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    first_retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    last_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class WorkSourceRow(Base):
    __tablename__ = "work_sources"
    __table_args__ = (
        UniqueConstraint(
            "work_id", "run_id", "query_id", "provider", name="uq_work_source_hit"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    work_id: Mapped[str] = mapped_column(
        ForeignKey("works.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("retrieval_runs.id", ondelete="CASCADE"), index=True
    )
    query_id: Mapped[str] = mapped_column(String(36), index=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    provider_record_id: Mapped[str] = mapped_column(Text)
    raw_record_id: Mapped[str] = mapped_column(
        ForeignKey("raw_records.id", ondelete="CASCADE")
    )
    matched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RetrievalRepository:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    def create_run(
        self,
        project_id: str,
        query_plan_version: int,
        queries: list[ProviderQuery],
        *,
        max_records_per_query: int | None,
    ) -> str:
        run_id = str(uuid4())
        now = utcnow()
        with self.session_factory.begin() as session:
            session.add(
                RetrievalRunRow(
                    id=run_id,
                    project_id=project_id,
                    query_plan_version=query_plan_version,
                    status=SearchRunStatus.RUNNING.value,
                    started_at=now,
                )
            )
            for query in queries:
                session.add(
                    QueryExecutionRow(
                        run_id=run_id,
                        query_id=str(query.query_id),
                        provider=query.provider.value,
                        purpose=query.purpose.value,
                        status=QueryExecutionStatus.PENDING.value,
                        cursor=None,
                        retrieved_count=0,
                        max_records=min(
                            query.max_records,
                            max_records_per_query or query.max_records,
                        ),
                        updated_at=now,
                    )
                )
            session.add(
                AuditEventRow(
                    project_id=project_id,
                    event_type="retrieval_run_started",
                    payload={
                        "run_id": run_id,
                        "query_plan_version": query_plan_version,
                        "query_count": len(queries),
                    },
                )
            )
        return run_id

    def execution_state(self, run_id: str, query_id: str) -> QueryExecutionRow:
        with self.session_factory() as session:
            row = session.scalar(
                select(QueryExecutionRow).where(
                    QueryExecutionRow.run_id == run_id,
                    QueryExecutionRow.query_id == query_id,
                )
            )
            if row is None:
                raise KeyError((run_id, query_id))
            session.expunge(row)
            return row

    def mark_query_running(self, run_id: str, query_id: str) -> None:
        with self.session_factory.begin() as session:
            row = self._execution(session, run_id, query_id)
            row.status = QueryExecutionStatus.RUNNING.value
            row.error = None
            row.updated_at = utcnow()

    def ingest_page(
        self,
        run_id: str,
        query: ProviderQuery,
        raw_items: list[dict[str, Any]],
        candidates: list[NormalizedWorkCandidate],
        *,
        next_cursor: str | None,
        estimated_total: int | None,
        reached_limit: bool,
    ) -> None:
        if len(raw_items) != len(candidates):
            raise ValueError("原始记录与规范化记录数量不一致")
        with self.session_factory.begin() as session:
            run = session.get(RetrievalRunRow, run_id)
            if run is None:
                raise KeyError(run_id)
            execution = self._execution(session, run_id, str(query.query_id))
            for raw, candidate in zip(raw_items, candidates, strict=True):
                existing_raw = session.scalar(
                    select(RawRecordRow).where(
                        RawRecordRow.run_id == run_id,
                        RawRecordRow.query_id == str(query.query_id),
                        RawRecordRow.provider_record_id
                        == candidate.provider_record_id,
                    )
                )
                if existing_raw is not None:
                    continue
                raw_id = str(uuid4())
                encoded = json.dumps(raw, ensure_ascii=False, sort_keys=True)
                session.add(
                    RawRecordRow(
                        id=raw_id,
                        run_id=run_id,
                        project_id=run.project_id,
                        query_id=str(query.query_id),
                        provider=query.provider.value,
                        provider_record_id=candidate.provider_record_id,
                        payload=raw,
                        response_hash=hashlib.sha256(
                            encoded.encode("utf-8")
                        ).hexdigest(),
                    )
                )
                work = self._find_work(session, run.project_id, candidate)
                if work is None:
                    work = self._new_work(session, run.project_id, candidate)
                else:
                    self._merge_work(work, candidate)
                session.flush()
                existing_hit = session.scalar(
                    select(WorkSourceRow).where(
                        WorkSourceRow.work_id == work.id,
                        WorkSourceRow.run_id == run_id,
                        WorkSourceRow.query_id == str(query.query_id),
                        WorkSourceRow.provider == query.provider.value,
                    )
                )
                if existing_hit is None:
                    session.add(
                        WorkSourceRow(
                            work_id=work.id,
                            run_id=run_id,
                            query_id=str(query.query_id),
                            provider=query.provider.value,
                            provider_record_id=candidate.provider_record_id,
                            raw_record_id=raw_id,
                        )
                    )
            execution.retrieved_count += len(raw_items)
            execution.estimated_total = estimated_total
            execution.cursor = next_cursor
            execution.status = (
                QueryExecutionStatus.COMPLETED.value
                if reached_limit or not next_cursor or not raw_items
                else QueryExecutionStatus.RUNNING.value
            )
            execution.updated_at = utcnow()

    def mark_query_failed(self, run_id: str, query_id: str, error: str) -> None:
        with self.session_factory.begin() as session:
            row = self._execution(session, run_id, query_id)
            row.status = QueryExecutionStatus.FAILED.value
            row.error = error[:4000]
            row.updated_at = utcnow()

    def finalize_run(self, run_id: str) -> SearchRun:
        with self.session_factory.begin() as session:
            run = session.get(RetrievalRunRow, run_id)
            if run is None:
                raise KeyError(run_id)
            states = session.scalars(
                select(QueryExecutionRow).where(QueryExecutionRow.run_id == run_id)
            ).all()
            failed = sum(item.status == QueryExecutionStatus.FAILED.value for item in states)
            completed = sum(
                item.status == QueryExecutionStatus.COMPLETED.value for item in states
            )
            if failed == len(states):
                run.status = SearchRunStatus.FAILED.value
            elif failed or completed < len(states):
                run.status = SearchRunStatus.PARTIAL.value
            else:
                run.status = SearchRunStatus.COMPLETED.value
            run.finished_at = utcnow()
            session.add(
                AuditEventRow(
                    project_id=run.project_id,
                    event_type="retrieval_run_finished",
                    payload={
                        "run_id": run_id,
                        "status": run.status,
                        "completed_queries": completed,
                        "failed_queries": failed,
                    },
                )
            )
        return self.get_run(run_id)

    def reopen_run(self, run_id: str) -> None:
        with self.session_factory.begin() as session:
            run = session.get(RetrievalRunRow, run_id)
            if run is None:
                raise KeyError(run_id)
            run.status = SearchRunStatus.RUNNING.value
            run.finished_at = None
            failed = session.scalars(
                select(QueryExecutionRow).where(
                    QueryExecutionRow.run_id == run_id,
                    QueryExecutionRow.status == QueryExecutionStatus.FAILED.value,
                )
            ).all()
            for row in failed:
                row.status = QueryExecutionStatus.PENDING.value
                row.error = None

    def get_run(self, run_id: str, project_id: str | None = None) -> SearchRun:
        with self.session_factory() as session:
            run = session.get(RetrievalRunRow, run_id)
            if run is None or (project_id is not None and run.project_id != project_id):
                raise KeyError(run_id)
            states = session.scalars(
                select(QueryExecutionRow)
                .where(QueryExecutionRow.run_id == run_id)
                .order_by(QueryExecutionRow.provider, QueryExecutionRow.query_id)
            ).all()
            raw_count = session.scalar(
                select(func.count(RawRecordRow.id)).where(
                    RawRecordRow.run_id == run_id
                )
            ) or 0
            work_count = session.scalar(
                select(func.count(func.distinct(WorkSourceRow.work_id))).where(
                    WorkSourceRow.run_id == run_id
                )
            ) or 0
            providers = sorted({row.provider for row in states})
            stats = []
            for provider in providers:
                provider_states = [row for row in states if row.provider == provider]
                stats.append(
                    ProviderRunStats(
                        provider=provider,
                        query_count=len(provider_states),
                        completed_queries=sum(
                            row.status == QueryExecutionStatus.COMPLETED.value
                            for row in provider_states
                        ),
                        failed_queries=sum(
                            row.status == QueryExecutionStatus.FAILED.value
                            for row in provider_states
                        ),
                        raw_record_count=sum(
                            row.retrieved_count for row in provider_states
                        ),
                    )
                )
            return SearchRun(
                run_id=UUID(run.id),
                project_id=UUID(run.project_id),
                query_plan_version=run.query_plan_version,
                status=run.status,
                query_executions=[
                    QueryExecution(
                        query_id=UUID(row.query_id),
                        provider=row.provider,
                        purpose=row.purpose,
                        status=row.status,
                        cursor=row.cursor,
                        retrieved_count=row.retrieved_count,
                        max_records=row.max_records,
                        estimated_total=row.estimated_total,
                        error=row.error,
                        updated_at=row.updated_at,
                    )
                    for row in states
                ],
                provider_stats=stats,
                raw_record_count=raw_count,
                work_count=work_count,
                error_count=sum(bool(row.error) for row in states),
                started_at=run.started_at,
                finished_at=run.finished_at,
            )

    def list_works(
        self,
        project_id: str,
        *,
        offset: int,
        limit: int,
        source: str | None = None,
        is_oa: bool | None = None,
    ) -> WorkPage:
        with self.session_factory() as session:
            statement = select(WorkRow).where(WorkRow.project_id == project_id)
            count_statement = select(func.count(WorkRow.id)).where(
                WorkRow.project_id == project_id
            )
            if is_oa is not None:
                statement = statement.where(WorkRow.is_oa == is_oa)
                count_statement = count_statement.where(WorkRow.is_oa == is_oa)
            if source:
                matching = select(WorkSourceRow.work_id).where(
                    WorkSourceRow.provider == source
                )
                statement = statement.where(WorkRow.id.in_(matching))
                count_statement = count_statement.where(WorkRow.id.in_(matching))
            total = session.scalar(count_statement) or 0
            rows = session.scalars(
                statement.order_by(
                    WorkRow.relevance_score.desc(), WorkRow.year.desc(), WorkRow.id
                )
                .offset(offset)
                .limit(limit)
            ).all()
            return WorkPage(
                project_id=UUID(project_id),
                total=total,
                offset=offset,
                limit=limit,
                items=[self._to_work(session, row) for row in rows],
            )

    def all_works(self, project_id: str) -> list[WorkView]:
        page = self.list_works(project_id, offset=0, limit=10000)
        return page.items

    @staticmethod
    def _execution(session: Session, run_id: str, query_id: str) -> QueryExecutionRow:
        row = session.scalar(
            select(QueryExecutionRow).where(
                QueryExecutionRow.run_id == run_id,
                QueryExecutionRow.query_id == query_id,
            )
        )
        if row is None:
            raise KeyError((run_id, query_id))
        return row

    def _find_work(
        self,
        session: Session,
        project_id: str,
        candidate: NormalizedWorkCandidate,
    ) -> WorkRow | None:
        if candidate.doi:
            row = session.scalar(
                select(WorkRow).where(
                    WorkRow.project_id == project_id, WorkRow.doi == candidate.doi
                )
            )
            if row:
                return row
        identifiers = [
            getattr(WorkRow, field) == getattr(candidate, field)
            for field in ("pmid", "pmcid", "arxiv_id", "openalex_id", "s2_id")
            if getattr(candidate, field)
        ]
        if identifiers:
            row = session.scalar(
                select(WorkRow).where(
                    WorkRow.project_id == project_id, or_(*identifiers)
                )
            )
            if row:
                return row
        normalized_title = normalize_title(candidate.title)
        if normalized_title in {"", "untitled"}:
            return None
        title_matches = session.scalars(
            select(WorkRow).where(
                WorkRow.project_id == project_id,
                WorkRow.normalized_title == normalized_title,
            )
        ).all()
        for row in title_matches:
            if _years_close(row.year, candidate.year):
                return row
        return None

    def _new_work(
        self,
        session: Session,
        project_id: str,
        candidate: NormalizedWorkCandidate,
    ) -> WorkRow:
        now = utcnow()
        possible_duplicate = self._possible_duplicate(session, project_id, candidate)
        row = WorkRow(
            id=str(uuid4()),
            project_id=project_id,
            normalized_title=normalize_title(candidate.title),
            citation_source=(
                candidate.provider.value if candidate.citation_count is not None else None
            ),
            citation_updated_at=now if candidate.citation_count is not None else None,
            possible_duplicate_of=possible_duplicate.id if possible_duplicate else None,
            first_retrieved_at=now,
            last_updated_at=now,
            user_decision=UserDecision.UNREVIEWED.value,
            tags=[],
            notes="",
            **candidate.model_dump(
                exclude={
                    "provider",
                    "provider_record_id",
                },
                mode="python",
            ),
        )
        session.add(row)
        return row

    @staticmethod
    def _merge_work(work: WorkRow, candidate: NormalizedWorkCandidate) -> None:
        for field in (
            "publication_date",
            "venue",
            "volume",
            "issue",
            "pages",
            "work_type",
            "language",
            "abstract",
            "doi",
            "pmid",
            "pmcid",
            "arxiv_id",
            "openalex_id",
            "s2_id",
            "oa_status",
            "license",
            "landing_url",
            "pdf_url",
        ):
            if getattr(work, field) in (None, "") and getattr(candidate, field) not in (
                None,
                "",
            ):
                setattr(work, field, getattr(candidate, field))
        if not work.authors and candidate.authors:
            work.authors = candidate.authors
        work.author_keywords = _union(work.author_keywords, candidate.author_keywords)
        work.subjects = _union(work.subjects, candidate.subjects)
        if candidate.year is not None and work.year is None:
            work.year = candidate.year
        if candidate.citation_count is not None and (
            work.citation_count is None or candidate.citation_count > work.citation_count
        ):
            work.citation_count = candidate.citation_count
            work.citation_source = candidate.provider.value
            work.citation_updated_at = utcnow()
        if candidate.is_retracted:
            work.is_retracted = True
            work.retraction_source = candidate.retraction_source
        if candidate.is_oa is True:
            work.is_oa = True
        elif work.is_oa is None:
            work.is_oa = candidate.is_oa
        if candidate.relevance_score > work.relevance_score:
            work.relevance_score = candidate.relevance_score
            work.relevance_reason = candidate.relevance_reason
        work.last_updated_at = utcnow()

    @staticmethod
    def _possible_duplicate(
        session: Session,
        project_id: str,
        candidate: NormalizedWorkCandidate,
    ) -> WorkRow | None:
        statement = select(WorkRow).where(WorkRow.project_id == project_id)
        if candidate.year is not None:
            statement = statement.where(
                WorkRow.year.between(candidate.year - 1, candidate.year + 1)
            )
        rows = session.scalars(statement.limit(500)).all()
        target = normalize_title(candidate.title)
        if target in {"", "untitled"}:
            return None
        first_author = normalize_title(candidate.authors[0]) if candidate.authors else ""
        for row in rows:
            row_author = normalize_title(row.authors[0]) if row.authors else ""
            if first_author and row_author and first_author != row_author:
                continue
            if difflib.SequenceMatcher(None, target, row.normalized_title).ratio() >= 0.94:
                return row
        return None

    @staticmethod
    def _to_work(session: Session, row: WorkRow) -> WorkView:
        hits = session.scalars(
            select(WorkSourceRow)
            .where(WorkSourceRow.work_id == row.id)
            .order_by(WorkSourceRow.id)
        ).all()
        return WorkView(
            work_id=UUID(row.id),
            project_id=UUID(row.project_id),
            title=row.title,
            authors=row.authors,
            year=row.year,
            publication_date=row.publication_date,
            venue=row.venue,
            volume=row.volume,
            issue=row.issue,
            pages=row.pages,
            work_type=row.work_type,
            language=row.language,
            abstract=row.abstract,
            author_keywords=row.author_keywords,
            subjects=row.subjects,
            doi=row.doi,
            pmid=row.pmid,
            pmcid=row.pmcid,
            arxiv_id=row.arxiv_id,
            openalex_id=row.openalex_id,
            s2_id=row.s2_id,
            sources=list(dict.fromkeys(hit.provider for hit in hits)),
            matched_query_ids=list(
                dict.fromkeys(UUID(hit.query_id) for hit in hits)
            ),
            citation_count=row.citation_count,
            citation_source=row.citation_source,
            citation_updated_at=row.citation_updated_at,
            is_retracted=row.is_retracted,
            retraction_source=row.retraction_source,
            relevance_score=row.relevance_score,
            relevance_reason=row.relevance_reason,
            possible_duplicate_of=(
                UUID(row.possible_duplicate_of) if row.possible_duplicate_of else None
            ),
            is_oa=row.is_oa,
            oa_status=row.oa_status,
            license=row.license,
            landing_url=row.landing_url,
            pdf_url=row.pdf_url,
            user_decision=row.user_decision,
            exclude_reason=row.exclude_reason,
            notes=row.notes,
            tags=row.tags,
            first_retrieved_at=row.first_retrieved_at,
            last_updated_at=row.last_updated_at,
        )


def normalize_title(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    value = unicodedata.normalize("NFKC", value).casefold()
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def _years_close(left: int | None, right: int | None) -> bool:
    return left is not None and right is not None and abs(left - right) <= 1


def _union(left: list[str] | None, right: list[str] | None) -> list[str]:
    return list(dict.fromkeys([*(left or []), *(right or [])]))
