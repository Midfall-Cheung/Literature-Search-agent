from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, delete, select
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker

from app.repositories.projects import AuditEventRow, Base, utcnow
from app.schemas.query_plans import (
    ProviderQuery,
    ProviderQueryDraft,
    QueryPlan,
    QueryPlanHistory,
    QueryPlanStatus,
    QueryPlanVersion,
)


class QueryPlanSetRow(Base):
    __tablename__ = "query_plan_sets"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(32), index=True)
    version: Mapped[int] = mapped_column(Integer)
    term_set_version: Mapped[int] = mapped_column(Integer)
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ProviderQueryRow(Base):
    __tablename__ = "provider_queries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    plan_version: Mapped[int] = mapped_column(Integer)
    term_set_version: Mapped[int] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    purpose: Mapped[str] = mapped_column(String(32))
    canonical_query: Mapped[str] = mapped_column(Text)
    provider_query: Mapped[str] = mapped_column(Text)
    endpoint: Mapped[str] = mapped_column(Text)
    request_params: Mapped[dict[str, Any]] = mapped_column(JSON)
    filters: Mapped[dict[str, Any]] = mapped_column(JSON)
    page_size: Mapped[int] = mapped_column(Integer)
    max_records: Mapped[int] = mapped_column(Integer)
    notes: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class QueryPlanVersionRow(Base):
    __tablename__ = "query_plan_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))
    term_set_version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class QueryPlanVersionConflictError(ValueError):
    pass


class QueryPlanRepository:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    def get(self, project_id: str) -> QueryPlan:
        with self.session_factory() as session:
            plan_set = session.get(QueryPlanSetRow, project_id)
            if plan_set is None:
                return QueryPlan(
                    project_id=UUID(project_id),
                    status=QueryPlanStatus.NOT_COMPILED,
                    version=0,
                    term_set_version=0,
                )
            rows = session.scalars(
                select(ProviderQueryRow)
                .where(ProviderQueryRow.project_id == project_id)
                .order_by(ProviderQueryRow.provider, ProviderQueryRow.purpose)
            ).all()
            return QueryPlan(
                project_id=UUID(project_id),
                status=QueryPlanStatus(plan_set.status),
                version=plan_set.version,
                term_set_version=plan_set.term_set_version,
                queries=[self._to_query(row) for row in rows],
                confirmed_at=plan_set.confirmed_at,
                created_at=plan_set.created_at,
                updated_at=plan_set.updated_at,
            )

    def replace(
        self,
        project_id: str,
        drafts: list[ProviderQueryDraft],
        *,
        term_set_version: int,
        expected_version: int,
    ) -> QueryPlan:
        with self.session_factory.begin() as session:
            plan_set = session.get(QueryPlanSetRow, project_id)
            actual_version = 0 if plan_set is None else plan_set.version
            if actual_version != expected_version:
                raise QueryPlanVersionConflictError(
                    f"查询计划版本冲突：期望 {expected_version}，实际 {actual_version}"
                )
            now = utcnow()
            next_version = actual_version + 1
            if plan_set is None:
                plan_set = QueryPlanSetRow(
                    project_id=project_id,
                    status=QueryPlanStatus.DRAFT.value,
                    version=next_version,
                    term_set_version=term_set_version,
                    created_at=now,
                    updated_at=now,
                )
                session.add(plan_set)
            else:
                plan_set.status = QueryPlanStatus.DRAFT.value
                plan_set.version = next_version
                plan_set.term_set_version = term_set_version
                plan_set.confirmed_at = None
                plan_set.updated_at = now
                session.execute(
                    delete(ProviderQueryRow).where(
                        ProviderQueryRow.project_id == project_id
                    )
                )
                session.flush()

            snapshot: list[dict[str, Any]] = []
            for draft in drafts:
                query_id = str(uuid4())
                data = draft.model_dump(mode="json")
                session.add(
                    ProviderQueryRow(
                        id=query_id,
                        project_id=project_id,
                        plan_version=next_version,
                        term_set_version=term_set_version,
                        created_at=now,
                        **data,
                    )
                )
                snapshot.append({"query_id": query_id, **data})
            session.add(
                QueryPlanVersionRow(
                    project_id=project_id,
                    version=next_version,
                    status=QueryPlanStatus.DRAFT.value,
                    term_set_version=term_set_version,
                    snapshot=snapshot,
                )
            )
            session.add(
                AuditEventRow(
                    project_id=project_id,
                    event_type="query_plan_compiled",
                    payload={
                        "version": next_version,
                        "term_set_version": term_set_version,
                        "query_count": len(drafts),
                    },
                )
            )
        return self.get(project_id)

    def confirm(self, project_id: str, *, expected_version: int) -> QueryPlan:
        with self.session_factory.begin() as session:
            plan_set = session.get(QueryPlanSetRow, project_id)
            if plan_set is None:
                raise ValueError("查询计划尚未编译")
            if plan_set.version != expected_version:
                raise QueryPlanVersionConflictError(
                    f"查询计划版本冲突：期望 {expected_version}，实际 {plan_set.version}"
                )
            if plan_set.status == QueryPlanStatus.CONFIRMED.value:
                return self.get(project_id)
            now = utcnow()
            plan_set.status = QueryPlanStatus.CONFIRMED.value
            plan_set.confirmed_at = now
            plan_set.updated_at = now
            rows = session.scalars(
                select(ProviderQueryRow)
                .where(ProviderQueryRow.project_id == project_id)
                .order_by(ProviderQueryRow.provider, ProviderQueryRow.purpose)
            ).all()
            snapshot = [self._snapshot(row) for row in rows]
            session.add(
                QueryPlanVersionRow(
                    project_id=project_id,
                    version=plan_set.version,
                    status=QueryPlanStatus.CONFIRMED.value,
                    term_set_version=plan_set.term_set_version,
                    snapshot=snapshot,
                )
            )
            session.add(
                AuditEventRow(
                    project_id=project_id,
                    event_type="query_plan_confirmed",
                    payload={
                        "version": plan_set.version,
                        "term_set_version": plan_set.term_set_version,
                    },
                )
            )
        return self.get(project_id)

    def history(self, project_id: str) -> QueryPlanHistory:
        with self.session_factory() as session:
            rows = session.scalars(
                select(QueryPlanVersionRow)
                .where(QueryPlanVersionRow.project_id == project_id)
                .order_by(QueryPlanVersionRow.version, QueryPlanVersionRow.id)
            ).all()
            return QueryPlanHistory(
                project_id=UUID(project_id),
                versions=[
                    QueryPlanVersion(
                        project_id=UUID(project_id),
                        version=row.version,
                        status=QueryPlanStatus(row.status),
                        term_set_version=row.term_set_version,
                        snapshot=row.snapshot,
                        created_at=row.created_at,
                    )
                    for row in rows
                ],
            )

    @staticmethod
    def _to_query(row: ProviderQueryRow) -> ProviderQuery:
        return ProviderQuery(
            query_id=UUID(row.id),
            project_id=UUID(row.project_id),
            plan_version=row.plan_version,
            term_set_version=row.term_set_version,
            provider=row.provider,
            purpose=row.purpose,
            canonical_query=row.canonical_query,
            provider_query=row.provider_query,
            endpoint=row.endpoint,
            request_params=row.request_params,
            filters=row.filters,
            page_size=row.page_size,
            max_records=row.max_records,
            notes=row.notes,
            created_at=row.created_at,
        )

    @staticmethod
    def _snapshot(row: ProviderQueryRow) -> dict[str, Any]:
        return {
            "query_id": row.id,
            "provider": row.provider,
            "purpose": row.purpose,
            "canonical_query": row.canonical_query,
            "provider_query": row.provider_query,
            "endpoint": row.endpoint,
            "request_params": row.request_params,
            "filters": row.filters,
            "page_size": row.page_size,
            "max_records": row.max_records,
            "notes": row.notes,
        }
