from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app.schemas.question import (
    ClarificationQuestion,
    MessageView,
    ProjectState,
    ProjectStatus,
    ResearchQuestionSpec,
)


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class ProjectRow(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    original_question: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), index=True)
    clarification_round: Mapped[int] = mapped_column(Integer, default=0)
    current_spec: Mapped[dict[str, Any]] = mapped_column(JSON)
    missing_fields: Mapped[list[str]] = mapped_column(JSON, default=list)
    current_question: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    assumptions: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class MessageRow(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    target_fields: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class QuestionSpecVersionRow(Base):
    __tablename__ = "question_specs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    spec: Mapped[dict[str, Any]] = mapped_column(JSON)
    spec_hash: Mapped[str] = mapped_column(String(64))
    confirmed: Mapped[bool]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditEventRow(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def create_engine_and_schema(database_url: str) -> tuple[Engine, sessionmaker[Session]]:
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, connect_args=connect_args)
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


class ProjectRepository:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    def create(self, original_question: str) -> str:
        project_id = str(uuid4())
        initial_spec = ResearchQuestionSpec(original_question=original_question)
        with self.session_factory.begin() as session:
            session.add(
                ProjectRow(
                    id=project_id,
                    original_question=original_question,
                    status=ProjectStatus.CLARIFYING.value,
                    clarification_round=0,
                    current_spec=initial_spec.model_dump(mode="json"),
                    missing_fields=[],
                    assumptions=[],
                )
            )
            session.add(
                MessageRow(
                    project_id=project_id,
                    role="user",
                    content=original_question,
                    target_fields=[],
                )
            )
            session.add(
                AuditEventRow(
                    project_id=project_id,
                    event_type="project_created",
                    payload={"original_question": original_question},
                )
            )
        return project_id

    def exists(self, project_id: str) -> bool:
        with self.session_factory() as session:
            return session.get(ProjectRow, project_id) is not None

    def add_user_message(
        self, project_id: str, content: str, target_fields: list[str]
    ) -> None:
        with self.session_factory.begin() as session:
            session.add(
                MessageRow(
                    project_id=project_id,
                    role="user",
                    content=content,
                    target_fields=target_fields,
                )
            )
            session.add(
                AuditEventRow(
                    project_id=project_id,
                    event_type="clarification_answered",
                    payload={"target_fields": target_fields},
                )
            )

    def add_confirmation_message(
        self, project_id: str, accepted: bool, feedback: str | None
    ) -> None:
        content = "确认结构化研究问题" if accepted else (feedback or "修改结构化研究问题")
        with self.session_factory.begin() as session:
            session.add(
                MessageRow(
                    project_id=project_id,
                    role="user",
                    content=content,
                    target_fields=[],
                )
            )
            session.add(
                AuditEventRow(
                    project_id=project_id,
                    event_type="question_confirmed" if accepted else "question_revision_requested",
                    payload={"accepted": accepted},
                )
            )

    def sync_graph_state(self, project_id: str, graph_state: dict[str, Any]) -> None:
        with self.session_factory.begin() as session:
            project = session.get(ProjectRow, project_id)
            if project is None:
                raise KeyError(project_id)

            project.status = graph_state["status"]
            project.clarification_round = graph_state.get("clarification_round", 0)
            project.current_spec = graph_state["question_spec"]
            project.missing_fields = graph_state.get("missing_fields", [])
            project.current_question = graph_state.get("current_question")
            project.summary = graph_state.get("summary")
            project.assumptions = graph_state.get("assumptions", [])
            project.updated_at = utcnow()

            self._save_version_if_changed(session, project)
            assistant_content, target_fields = self._assistant_message_for(project)
            if assistant_content and not self._is_duplicate_last_assistant(
                session, project_id, assistant_content
            ):
                session.add(
                    MessageRow(
                        project_id=project_id,
                        role="assistant",
                        content=assistant_content,
                        target_fields=target_fields,
                    )
                )
            session.add(
                AuditEventRow(
                    project_id=project_id,
                    event_type="workflow_state_changed",
                    payload={
                        "status": project.status,
                        "clarification_round": project.clarification_round,
                        "missing_fields": project.missing_fields,
                    },
                )
            )

    def get_state(self, project_id: str) -> ProjectState:
        with self.session_factory() as session:
            project = session.get(ProjectRow, project_id)
            if project is None:
                raise KeyError(project_id)
            rows = session.scalars(
                select(MessageRow)
                .where(MessageRow.project_id == project_id)
                .order_by(MessageRow.id)
            ).all()
            messages = [
                MessageView(
                    role=row.role,
                    content=row.content,
                    target_fields=row.target_fields,
                    created_at=row.created_at,
                )
                for row in rows
            ]
            return ProjectState(
                project_id=UUID(project.id),
                status=ProjectStatus(project.status),
                clarification_round=project.clarification_round,
                question_spec=ResearchQuestionSpec.model_validate(project.current_spec),
                missing_fields=project.missing_fields,
                current_question=(
                    ClarificationQuestion.model_validate(project.current_question)
                    if project.current_question
                    else None
                ),
                summary=project.summary,
                assumptions=project.assumptions,
                messages=messages,
                created_at=project.created_at,
                updated_at=project.updated_at,
            )

    @staticmethod
    def _assistant_message_for(project: ProjectRow) -> tuple[str | None, list[str]]:
        if project.current_question:
            return (
                str(project.current_question["prompt"]),
                list(project.current_question["target_fields"]),
            )
        if project.summary and project.status == ProjectStatus.AWAITING_CONFIRMATION.value:
            return f"请确认以下结构化研究问题：\n{project.summary}", []
        if project.summary and project.status == ProjectStatus.CONFIRMED.value:
            return "结构化研究问题已确认，阶段 A 已完成。", []
        return None, []

    @staticmethod
    def _is_duplicate_last_assistant(
        session: Session, project_id: str, content: str
    ) -> bool:
        row = session.scalar(
            select(MessageRow)
            .where(MessageRow.project_id == project_id, MessageRow.role == "assistant")
            .order_by(MessageRow.id.desc())
            .limit(1)
        )
        return row is not None and row.content == content

    @staticmethod
    def _save_version_if_changed(session: Session, project: ProjectRow) -> None:
        encoded = json.dumps(project.current_spec, ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        latest = session.scalar(
            select(QuestionSpecVersionRow)
            .where(QuestionSpecVersionRow.project_id == project.id)
            .order_by(QuestionSpecVersionRow.version.desc())
            .limit(1)
        )
        if latest is not None and latest.spec_hash == digest:
            return
        session.add(
            QuestionSpecVersionRow(
                project_id=project.id,
                version=1 if latest is None else latest.version + 1,
                spec=project.current_spec,
                spec_hash=digest,
                confirmed=bool(project.current_spec.get("confirmed")),
            )
        )

