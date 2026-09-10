from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    delete,
    select,
)
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker

from app.repositories.projects import AuditEventRow, Base, utcnow
from app.schemas.terms import (
    ConceptSummary,
    TermCandidate,
    TermInput,
    TermRecord,
    TermSetStatus,
    TermSetVersion,
    TermHistory,
    TermTable,
)
from app.services.term_builder import normalize_term


class TermSetRow(Base):
    __tablename__ = "term_sets"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(32), index=True)
    version: Mapped[int] = mapped_column(Integer)
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class TermRow(Base):
    __tablename__ = "terms"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "concept_id", "normalized_term", name="uq_project_concept_term"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    concept_id: Mapped[str] = mapped_column(String(32), index=True)
    concept_name: Mapped[str] = mapped_column(String(200))
    term: Mapped[str] = mapped_column(Text)
    normalized_term: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(16))
    term_type: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(32))
    field_hint: Mapped[str] = mapped_column(String(32))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    sort_order: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class TermSetVersionRow(Base):
    __tablename__ = "term_set_versions"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "version", "status", name="uq_term_set_version_status"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))
    snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TermVersionConflictError(ValueError):
    pass


class TermRepository:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    def get(self, project_id: str) -> TermTable:
        with self.session_factory() as session:
            term_set = session.get(TermSetRow, project_id)
            if term_set is None:
                return TermTable(
                    project_id=UUID(project_id),
                    status=TermSetStatus.NOT_GENERATED,
                    version=0,
                )
            rows = session.scalars(
                select(TermRow)
                .where(TermRow.project_id == project_id)
                .order_by(TermRow.sort_order, TermRow.id)
            ).all()
            terms = [self._to_record(row) for row in rows]
            return TermTable(
                project_id=UUID(project_id),
                status=TermSetStatus(term_set.status),
                version=term_set.version,
                concepts=self._concept_summaries(terms),
                terms=terms,
                confirmed_at=term_set.confirmed_at,
                created_at=term_set.created_at,
                updated_at=term_set.updated_at,
            )

    def replace(
        self,
        project_id: str,
        terms: list[TermCandidate | TermInput],
        *,
        expected_version: int,
        event_type: str,
    ) -> TermTable:
        with self.session_factory.begin() as session:
            term_set = session.get(TermSetRow, project_id)
            actual_version = 0 if term_set is None else term_set.version
            if actual_version != expected_version:
                raise TermVersionConflictError(
                    f"词表版本冲突：期望 {expected_version}，实际 {actual_version}"
                )
            now = utcnow()
            next_version = actual_version + 1
            existing_ids = set(
                session.scalars(
                    select(TermRow.id).where(TermRow.project_id == project_id)
                ).all()
            )
            supplied_ids = {
                str(item.term_id)
                for item in terms
                if isinstance(item, TermInput) and item.term_id is not None
            }
            foreign_ids = supplied_ids - existing_ids
            if foreign_ids:
                raise ValueError("term_id 必须属于当前项目；新增词请省略 term_id")
            if term_set is None:
                term_set = TermSetRow(
                    project_id=project_id,
                    status=TermSetStatus.DRAFT.value,
                    version=next_version,
                    created_at=now,
                    updated_at=now,
                )
                session.add(term_set)
            else:
                term_set.status = TermSetStatus.DRAFT.value
                term_set.version = next_version
                term_set.confirmed_at = None
                term_set.updated_at = now
                session.execute(delete(TermRow).where(TermRow.project_id == project_id))
                session.flush()

            snapshot: list[dict[str, Any]] = []
            for index, item in enumerate(terms):
                item_data = item.model_dump(mode="json")
                term_id = str(item_data.pop("term_id", None) or uuid4())
                normalized = normalize_term(str(item_data["term"]))
                row = TermRow(
                    id=term_id,
                    project_id=project_id,
                    normalized_term=normalized,
                    sort_order=index,
                    created_at=now,
                    updated_at=now,
                    **item_data,
                )
                session.add(row)
                snapshot.append(
                    {
                        "term_id": term_id,
                        "normalized_term": normalized,
                        **item_data,
                    }
                )
            session.add(
                TermSetVersionRow(
                    project_id=project_id,
                    version=next_version,
                    status=TermSetStatus.DRAFT.value,
                    snapshot=snapshot,
                )
            )
            session.add(
                AuditEventRow(
                    project_id=project_id,
                    event_type=event_type,
                    payload={"version": next_version, "term_count": len(snapshot)},
                )
            )
        return self.get(project_id)

    def history(self, project_id: str) -> TermHistory:
        with self.session_factory() as session:
            rows = session.scalars(
                select(TermSetVersionRow)
                .where(TermSetVersionRow.project_id == project_id)
                .order_by(TermSetVersionRow.version, TermSetVersionRow.id)
            ).all()
            return TermHistory(
                project_id=UUID(project_id),
                versions=[
                    TermSetVersion(
                        project_id=UUID(project_id),
                        version=row.version,
                        status=TermSetStatus(row.status),
                        snapshot=row.snapshot,
                        created_at=row.created_at,
                    )
                    for row in rows
                ],
            )

    def confirm(self, project_id: str, *, expected_version: int) -> TermTable:
        with self.session_factory.begin() as session:
            term_set = session.get(TermSetRow, project_id)
            if term_set is None:
                raise ValueError("词表尚未生成")
            if term_set.version != expected_version:
                raise TermVersionConflictError(
                    f"词表版本冲突：期望 {expected_version}，实际 {term_set.version}"
                )
            now = utcnow()
            term_set.status = TermSetStatus.CONFIRMED.value
            term_set.confirmed_at = now
            term_set.updated_at = now
            rows = session.scalars(
                select(TermRow)
                .where(TermRow.project_id == project_id)
                .order_by(TermRow.sort_order)
            ).all()
            snapshot = [self._snapshot_row(row) for row in rows]
            session.add(
                TermSetVersionRow(
                    project_id=project_id,
                    version=term_set.version,
                    status=TermSetStatus.CONFIRMED.value,
                    snapshot=snapshot,
                )
            )
            session.add(
                AuditEventRow(
                    project_id=project_id,
                    event_type="terms_confirmed",
                    payload={"version": term_set.version, "term_count": len(rows)},
                )
            )
        return self.get(project_id)

    @staticmethod
    def _to_record(row: TermRow) -> TermRecord:
        return TermRecord(
            term_id=UUID(row.id),
            concept_id=row.concept_id,
            concept_name=row.concept_name,
            term=row.term,
            normalized_term=row.normalized_term,
            language=row.language,
            term_type=row.term_type,
            source=row.source,
            field_hint=row.field_hint,
            enabled=row.enabled,
            notes=row.notes,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _concept_summaries(terms: list[TermRecord]) -> list[ConceptSummary]:
        grouped: dict[tuple[str, str], list[TermRecord]] = {}
        for term in terms:
            grouped.setdefault((term.concept_id, term.concept_name), []).append(term)
        return [
            ConceptSummary(
                concept_id=concept_id,
                concept_name=concept_name,
                enabled_term_count=sum(item.enabled for item in items),
                total_term_count=len(items),
            )
            for (concept_id, concept_name), items in grouped.items()
        ]

    @staticmethod
    def _snapshot_row(row: TermRow) -> dict[str, Any]:
        return {
            "term_id": row.id,
            "concept_id": row.concept_id,
            "concept_name": row.concept_name,
            "term": row.term,
            "normalized_term": row.normalized_term,
            "language": row.language,
            "term_type": row.term_type,
            "source": row.source,
            "field_hint": row.field_hint,
            "enabled": row.enabled,
            "notes": row.notes,
        }
