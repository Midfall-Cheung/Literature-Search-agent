from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    select,
)
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker

from app.repositories.projects import AuditEventRow, Base, utcnow
from app.repositories.retrieval import WorkRow
from app.schemas.downloads import (
    DownloadAttempt,
    DownloadAttemptStatus,
    DownloadItem,
    DownloadItemStatus,
    DownloadRequest,
    DownloadedFile,
    DownloadRun,
    DownloadRunStatus,
    FullTextSource,
    ResolvedLocation,
)


class DownloadRunRow(Base):
    __tablename__ = "download_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), index=True)
    mode: Mapped[str] = mapped_column(String(32))
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    papers_directory: Mapped[str] = mapped_column(Text)
    manifest_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    failed_report_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class DownloadItemRow(Base):
    __tablename__ = "download_items"
    __table_args__ = (
        UniqueConstraint("run_id", "work_id", name="uq_download_run_work"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("download_runs.id", ondelete="CASCADE"), index=True
    )
    work_id: Mapped[str] = mapped_column(
        ForeignKey("works.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), index=True)
    file_id: Mapped[str | None] = mapped_column(
        ForeignKey("files.id"), nullable=True
    )
    landing_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class DownloadAttemptRow(Base):
    __tablename__ = "download_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("download_runs.id", ondelete="CASCADE"), index=True
    )
    work_id: Mapped[str] = mapped_column(
        ForeignKey("works.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class FileRow(Base):
    __tablename__ = "files"
    __table_args__ = (
        UniqueConstraint("project_id", "sha256", name="uq_project_file_sha256"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    local_path: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(Integer)
    mime_type: Mapped[str] = mapped_column(String(128))
    source: Mapped[str] = mapped_column(String(32))
    source_url: Mapped[str] = mapped_column(Text)
    final_url: Mapped[str] = mapped_column(Text)
    license: Mapped[str | None] = mapped_column(Text, nullable=True)
    version_type: Mapped[str] = mapped_column(String(32))
    downloaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class WorkFileRow(Base):
    __tablename__ = "work_files"
    __table_args__ = (
        UniqueConstraint("work_id", "file_id", name="uq_work_file"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    work_id: Mapped[str] = mapped_column(
        ForeignKey("works.id", ondelete="CASCADE"), index=True
    )
    file_id: Mapped[str] = mapped_column(
        ForeignKey("files.id", ondelete="CASCADE"), index=True
    )
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DownloadRepository:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    def create_run(
        self,
        project_id: str,
        request: DownloadRequest,
        work_ids: list[str],
        papers_directory: Path,
    ) -> str:
        run_id = str(uuid4())
        with self.session_factory.begin() as session:
            session.add(
                DownloadRunRow(
                    id=run_id,
                    project_id=project_id,
                    status=DownloadRunStatus.RUNNING.value,
                    mode=request.mode.value,
                    request_payload=request.model_dump(mode="json"),
                    papers_directory=str(papers_directory.resolve()),
                )
            )
            for work_id in work_ids:
                session.add(
                    DownloadItemRow(
                        run_id=run_id,
                        work_id=work_id,
                        status=DownloadItemStatus.PENDING.value,
                    )
                )
            session.add(
                AuditEventRow(
                    project_id=project_id,
                    event_type="download_run_started",
                    payload={
                        "run_id": run_id,
                        "mode": request.mode.value,
                        "work_count": len(work_ids),
                    },
                )
            )
        return run_id

    def get_request(self, run_id: str) -> DownloadRequest:
        with self.session_factory() as session:
            row = session.get(DownloadRunRow, run_id)
            if row is None:
                raise KeyError(run_id)
            return DownloadRequest.model_validate(row.request_payload)

    def reopen(self, run_id: str) -> None:
        with self.session_factory.begin() as session:
            run = session.get(DownloadRunRow, run_id)
            if run is None:
                raise KeyError(run_id)
            run.status = DownloadRunStatus.RUNNING.value
            run.finished_at = None
            items = session.scalars(
                select(DownloadItemRow).where(
                    DownloadItemRow.run_id == run_id,
                    DownloadItemRow.status.in_(
                        [
                            DownloadItemStatus.PENDING.value,
                            DownloadItemStatus.FAILED.value,
                            DownloadItemStatus.MANUAL_ACCESS_REQUIRED.value,
                        ]
                    ),
                )
            ).all()
            for item in items:
                item.status = DownloadItemStatus.PENDING.value
                item.error = None

    def pending_work_ids(self, run_id: str) -> list[str]:
        with self.session_factory() as session:
            return list(
                session.scalars(
                    select(DownloadItemRow.work_id).where(
                        DownloadItemRow.run_id == run_id,
                        DownloadItemRow.status == DownloadItemStatus.PENDING.value,
                    )
                ).all()
            )

    def existing_file_for_work(
        self, project_id: str, work_id: str
    ) -> DownloadedFile | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(FileRow)
                .join(WorkFileRow, WorkFileRow.file_id == FileRow.id)
                .where(
                    FileRow.project_id == project_id,
                    WorkFileRow.work_id == work_id,
                )
                .order_by(FileRow.downloaded_at.desc())
            )
            if row is None or not Path(row.local_path).is_file():
                return None
            return self._to_file(row)

    def file_by_sha(self, project_id: str, sha256: str) -> DownloadedFile | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(FileRow).where(
                    FileRow.project_id == project_id,
                    FileRow.sha256 == sha256,
                )
            )
            if row is None or not Path(row.local_path).is_file():
                return None
            return self._to_file(row)

    def save_file(
        self,
        project_id: str,
        work_id: str,
        *,
        sha256: str,
        local_path: Path,
        size_bytes: int,
        mime_type: str,
        location: ResolvedLocation,
        final_url: str,
    ) -> DownloadedFile:
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(FileRow).where(
                    FileRow.project_id == project_id,
                    FileRow.sha256 == sha256,
                )
            )
            if row is None:
                row = FileRow(
                    id=str(uuid4()),
                    project_id=project_id,
                    sha256=sha256,
                    local_path=str(local_path.resolve()),
                    size_bytes=size_bytes,
                    mime_type=mime_type,
                    source=location.source.value,
                    source_url=location.url,
                    final_url=final_url,
                    license=location.license,
                    version_type=location.version_type.value,
                    downloaded_at=utcnow(),
                )
                session.add(row)
                session.flush()
            self._link_work_file(session, work_id, row.id)
            result = self._to_file(row)
        return result

    def link_existing_file(self, work_id: str, file_id: UUID) -> None:
        with self.session_factory.begin() as session:
            self._link_work_file(session, work_id, str(file_id))

    def start_attempt(
        self, run_id: str, work_id: str, source: FullTextSource
    ) -> str:
        attempt_id = str(uuid4())
        with self.session_factory.begin() as session:
            session.add(
                DownloadAttemptRow(
                    id=attempt_id,
                    run_id=run_id,
                    work_id=work_id,
                    source=source.value,
                    status=DownloadAttemptStatus.RESOLVING.value,
                )
            )
        return attempt_id

    def finish_attempt(
        self,
        attempt_id: str,
        status: DownloadAttemptStatus,
        *,
        source_url: str | None = None,
        final_url: str | None = None,
        http_status: int | None = None,
        error: str | None = None,
    ) -> None:
        with self.session_factory.begin() as session:
            row = session.get(DownloadAttemptRow, attempt_id)
            if row is None:
                raise KeyError(attempt_id)
            row.status = status.value
            row.source_url = source_url
            row.final_url = final_url
            row.http_status = http_status
            row.error = error[:2000] if error else None
            row.finished_at = utcnow()

    def mark_item(
        self,
        run_id: str,
        work_id: str,
        status: DownloadItemStatus,
        *,
        file_id: UUID | None = None,
        landing_url: str | None = None,
        error: str | None = None,
    ) -> None:
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(DownloadItemRow).where(
                    DownloadItemRow.run_id == run_id,
                    DownloadItemRow.work_id == work_id,
                )
            )
            if row is None:
                raise KeyError((run_id, work_id))
            row.status = status.value
            row.file_id = str(file_id) if file_id else None
            row.landing_url = landing_url
            row.error = error[:2000] if error else None

    def finalize(
        self,
        run_id: str,
        *,
        manifest_path: Path,
        failed_report_path: Path,
    ) -> DownloadRun:
        with self.session_factory.begin() as session:
            run = session.get(DownloadRunRow, run_id)
            if run is None:
                raise KeyError(run_id)
            statuses = list(
                session.scalars(
                    select(DownloadItemRow.status).where(
                        DownloadItemRow.run_id == run_id
                    )
                ).all()
            )
            successes = sum(
                value
                in {
                    DownloadItemStatus.DOWNLOADED.value,
                    DownloadItemStatus.ALREADY_EXISTS.value,
                }
                for value in statuses
            )
            incomplete = len(statuses) - successes
            run.status = (
                DownloadRunStatus.COMPLETED.value
                if incomplete == 0
                else DownloadRunStatus.PARTIAL.value
                if successes > 0
                else DownloadRunStatus.FAILED.value
            )
            run.manifest_path = str(manifest_path.resolve())
            run.failed_report_path = str(failed_report_path.resolve())
            run.finished_at = utcnow()
            session.add(
                AuditEventRow(
                    project_id=run.project_id,
                    event_type="download_run_finished",
                    payload={
                        "run_id": run_id,
                        "status": run.status,
                        "success_count": successes,
                        "incomplete_count": incomplete,
                    },
                )
            )
        return self.get_run(run_id)

    def get_run(self, run_id: str, project_id: str | None = None) -> DownloadRun:
        with self.session_factory() as session:
            run = session.get(DownloadRunRow, run_id)
            if run is None or (project_id is not None and run.project_id != project_id):
                raise KeyError(run_id)
            item_rows = session.execute(
                select(DownloadItemRow, WorkRow)
                .join(WorkRow, WorkRow.id == DownloadItemRow.work_id)
                .where(DownloadItemRow.run_id == run_id)
                .order_by(DownloadItemRow.id)
            ).all()
            attempt_rows = session.scalars(
                select(DownloadAttemptRow)
                .where(DownloadAttemptRow.run_id == run_id)
                .order_by(DownloadAttemptRow.started_at, DownloadAttemptRow.id)
            ).all()
            items = [
                DownloadItem(
                    work_id=UUID(item.work_id),
                    title=work.title,
                    status=item.status,
                    attempt_count=sum(row.work_id == item.work_id for row in attempt_rows),
                    file=(
                        self._to_file(session.get(FileRow, item.file_id))
                        if item.file_id
                        else None
                    ),
                    landing_url=item.landing_url or work.landing_url,
                    error=item.error,
                )
                for item, work in item_rows
            ]
            counts = {status.value: 0 for status in DownloadItemStatus}
            for item in items:
                counts[item.status.value] += 1
            return DownloadRun(
                run_id=UUID(run.id),
                project_id=UUID(run.project_id),
                status=run.status,
                mode=run.mode,
                total_count=len(items),
                downloaded_count=counts[DownloadItemStatus.DOWNLOADED.value],
                already_exists_count=counts[
                    DownloadItemStatus.ALREADY_EXISTS.value
                ],
                manual_access_required_count=counts[
                    DownloadItemStatus.MANUAL_ACCESS_REQUIRED.value
                ],
                failed_count=counts[DownloadItemStatus.FAILED.value],
                papers_directory=run.papers_directory,
                manifest_path=run.manifest_path,
                failed_report_path=run.failed_report_path,
                items=items,
                attempts=[
                    DownloadAttempt(
                        attempt_id=UUID(row.id),
                        work_id=UUID(row.work_id),
                        source=row.source,
                        status=row.status,
                        source_url=row.source_url,
                        final_url=row.final_url,
                        http_status=row.http_status,
                        error=row.error,
                        started_at=row.started_at,
                        finished_at=row.finished_at,
                    )
                    for row in attempt_rows
                ],
                started_at=run.started_at,
                finished_at=run.finished_at,
            )

    @staticmethod
    def _link_work_file(session: Session, work_id: str, file_id: str) -> None:
        existing = session.scalar(
            select(WorkFileRow).where(
                WorkFileRow.work_id == work_id,
                WorkFileRow.file_id == file_id,
            )
        )
        if existing is None:
            session.add(WorkFileRow(work_id=work_id, file_id=file_id))

    @staticmethod
    def _to_file(row: FileRow | None) -> DownloadedFile | None:
        if row is None:
            return None
        return DownloadedFile(
            file_id=UUID(row.id),
            sha256=row.sha256,
            local_path=row.local_path,
            size_bytes=row.size_bytes,
            mime_type=row.mime_type,
            source=row.source,
            source_url=row.source_url,
            final_url=row.final_url,
            license=row.license,
            version_type=row.version_type,
            downloaded_at=row.downloaded_at,
        )
