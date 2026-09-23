from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import os
import re
from pathlib import Path
from uuid import UUID

from app.repositories.downloads import DownloadRepository
from app.repositories.projects import ProjectRepository
from app.repositories.retrieval import RetrievalRepository
from app.resolvers.fulltext import FullTextResolver
from app.schemas.downloads import (
    DownloadAttemptStatus,
    DownloadEstimate,
    DownloadItemStatus,
    DownloadMode,
    DownloadRequest,
    DownloadRun,
    FullTextSource,
)
from app.schemas.retrieval import WorkView
from app.services.pdf_downloader import (
    InvalidPdfError,
    PdfHttpClient,
    PdfPermissionError,
    UnsafeDownloadUrlError,
    redact_url,
    safe_pdf_filename,
)
from app.services.projects import InvalidTransitionError, ProjectNotFoundError


DEFAULT_ESTIMATED_PDF_BYTES = 5 * 1024 * 1024


class DownloadRunNotFoundError(KeyError):
    pass


class DownloadService:
    def __init__(
        self,
        project_repository: ProjectRepository,
        retrieval_repository: RetrievalRepository,
        download_repository: DownloadRepository,
        resolvers: list[FullTextResolver],
        pdf_client: PdfHttpClient,
        *,
        projects_root: Path,
        max_file_bytes: int,
        concurrency: int = 3,
    ):
        self.project_repository = project_repository
        self.retrieval_repository = retrieval_repository
        self.repository = download_repository
        self.resolvers = resolvers
        self.pdf_client = pdf_client
        self.projects_root = projects_root
        self.max_file_bytes = max_file_bytes
        self.concurrency = max(1, concurrency)
        self._file_commit_lock = asyncio.Lock()

    def estimate(self, project_id: str, request: DownloadRequest) -> DownloadEstimate:
        works = self._select_works(project_id, request)
        already = sum(
            self.repository.existing_file_for_work(project_id, str(work.work_id))
            is not None
            for work in works
        )
        new_files = max(0, len(works) - already)
        notes = ["磁盘占用按每篇 5 MiB 粗略估算，实际大小以下载结果为准。"]
        if request.mode == DownloadMode.ALL_OA:
            notes.append("all_oa 必须在查看估算后显式设置 confirm_all_oa=true。")
        return DownloadEstimate(
            project_id=UUID(project_id),
            mode=request.mode,
            eligible_count=len(works),
            already_downloaded_count=already,
            estimated_new_files=new_files,
            estimated_bytes=new_files * DEFAULT_ESTIMATED_PDF_BYTES,
            requires_confirmation=(
                request.mode == DownloadMode.ALL_OA and not request.confirm_all_oa
            ),
            notes=notes,
        )

    async def start(self, project_id: str, request: DownloadRequest) -> DownloadRun:
        if request.mode == DownloadMode.ALL_OA and not request.confirm_all_oa:
            raise InvalidTransitionError(
                "all_oa 必须先查看估算并设置 confirm_all_oa=true"
            )
        works = self._select_works(project_id, request)
        if not works:
            raise InvalidTransitionError("当前策略没有选中可处理的文献")
        papers_directory, _ = self._output_directories(project_id)
        papers_directory.mkdir(parents=True, exist_ok=True)
        run_id = self.repository.create_run(
            project_id,
            request,
            [str(work.work_id) for work in works],
            papers_directory,
        )
        return await self._execute(project_id, run_id, works)

    async def resume(self, project_id: str, run_id: str) -> DownloadRun:
        self._project(project_id)
        try:
            current = self.repository.get_run(run_id, project_id)
        except KeyError as exc:
            raise DownloadRunNotFoundError(run_id) from exc
        if current.status.value == "completed":
            return current
        self.repository.reopen(run_id)
        all_works = {
            str(work.work_id): work
            for work in self.retrieval_repository.all_works(project_id)
        }
        pending = self.repository.pending_work_ids(run_id)
        works = [all_works[work_id] for work_id in pending if work_id in all_works]
        return await self._execute(project_id, run_id, works)

    def get(self, project_id: str, run_id: str) -> DownloadRun:
        self._project(project_id)
        try:
            return self.repository.get_run(run_id, project_id)
        except KeyError as exc:
            raise DownloadRunNotFoundError(run_id) from exc

    def report(self, project_id: str, run_id: str, kind: str) -> tuple[str, bytes, str]:
        run = self.get(project_id, run_id)
        path_value = run.manifest_path if kind == "manifest" else run.failed_report_path
        if not path_value:
            raise InvalidTransitionError("下载运行尚未生成报告")
        path = Path(path_value)
        if not path.is_file():
            raise InvalidTransitionError("报告文件不存在")
        expected_root = (self.projects_root / project_id).resolve()
        try:
            path.resolve().relative_to(expected_root)
        except ValueError as exc:
            raise InvalidTransitionError("报告路径不在项目目录内") from exc
        media_type = "application/x-ndjson" if kind == "manifest" else "text/csv"
        return path.name, path.read_bytes(), media_type

    async def _execute(
        self, project_id: str, run_id: str, works: list[WorkView]
    ) -> DownloadRun:
        papers_directory, manifest_directory = self._output_directories(project_id)
        papers_directory.mkdir(parents=True, exist_ok=True)
        manifest_directory.mkdir(parents=True, exist_ok=True)
        semaphore = asyncio.Semaphore(self.concurrency)

        async def guarded(work: WorkView) -> None:
            async with semaphore:
                await self._download_work(project_id, run_id, work, papers_directory)

        await asyncio.gather(*(guarded(work) for work in works))
        manifest_path = manifest_directory / f"downloads_{run_id}.jsonl"
        failed_path = manifest_directory / f"failed_downloads_{run_id}.csv"
        self._write_reports(run_id, manifest_path, failed_path)
        return self.repository.finalize(
            run_id,
            manifest_path=manifest_path,
            failed_report_path=failed_path,
        )

    async def _download_work(
        self,
        project_id: str,
        run_id: str,
        work: WorkView,
        papers_directory: Path,
    ) -> None:
        work_id = str(work.work_id)
        existing = self.repository.existing_file_for_work(project_id, work_id)
        if existing and _valid_local_file(existing.local_path, existing.sha256):
            self.repository.mark_item(
                run_id,
                work_id,
                DownloadItemStatus.ALREADY_EXISTS,
                file_id=existing.file_id,
                landing_url=_redact_optional(work.landing_url),
            )
            return

        technical_error: str | None = None
        for resolver in self.resolvers:
            attempt_id = self.repository.start_attempt(run_id, work_id, resolver.source)
            location = None
            part_path = papers_directory / f".{run_id}_{work_id}_{attempt_id}.part"
            try:
                location = await resolver.resolve(work)
                if location is None:
                    self.repository.finish_attempt(
                        attempt_id, DownloadAttemptStatus.NOT_AVAILABLE
                    )
                    continue
                result = await self.pdf_client.fetch_pdf(
                    location.url,
                    part_path,
                    max_bytes=self.max_file_bytes,
                )
                stored_location = location.model_copy(
                    update={
                        "url": redact_url(location.url),
                        "landing_url": (
                            redact_url(location.landing_url)
                            if location.landing_url
                            else None
                        ),
                    }
                )
                safe_final_url = redact_url(result.final_url)
                async with self._file_commit_lock:
                    duplicate = self.repository.file_by_sha(project_id, result.sha256)
                    if duplicate and _valid_local_file(
                        duplicate.local_path, duplicate.sha256
                    ):
                        part_path.unlink(missing_ok=True)
                        self.repository.link_existing_file(work_id, duplicate.file_id)
                        saved_file = duplicate
                        item_status = DownloadItemStatus.ALREADY_EXISTS
                    elif duplicate:
                        os.replace(part_path, duplicate.local_path)
                        self.repository.link_existing_file(work_id, duplicate.file_id)
                        saved_file = duplicate
                        item_status = DownloadItemStatus.DOWNLOADED
                    else:
                        final_path = self._available_final_path(
                            papers_directory, work, result.sha256
                        )
                        os.replace(part_path, final_path)
                        try:
                            saved_file = self.repository.save_file(
                                project_id,
                                work_id,
                                sha256=result.sha256,
                                local_path=final_path,
                                size_bytes=result.size_bytes,
                                mime_type=result.content_type,
                                location=stored_location,
                                final_url=safe_final_url,
                            )
                        except Exception:
                            final_path.unlink(missing_ok=True)
                            raise
                        item_status = DownloadItemStatus.DOWNLOADED
                self.repository.finish_attempt(
                    attempt_id,
                    DownloadAttemptStatus.DOWNLOADED,
                    source_url=redact_url(location.url),
                    final_url=safe_final_url,
                    http_status=result.http_status,
                )
                self.repository.mark_item(
                    run_id,
                    work_id,
                    item_status,
                    file_id=saved_file.file_id,
                    landing_url=(
                        stored_location.landing_url
                        or _redact_optional(work.landing_url)
                    ),
                )
                return
            except PdfPermissionError as exc:
                self.repository.finish_attempt(
                    attempt_id,
                    DownloadAttemptStatus.PERMISSION_DENIED,
                    source_url=redact_url(location.url) if location else None,
                    error=_safe_error(exc),
                )
            except InvalidPdfError as exc:
                self.repository.finish_attempt(
                    attempt_id,
                    DownloadAttemptStatus.INVALID_PDF,
                    source_url=redact_url(location.url) if location else None,
                    error=_safe_error(exc),
                )
            except UnsafeDownloadUrlError as exc:
                self.repository.finish_attempt(
                    attempt_id,
                    DownloadAttemptStatus.FAILED,
                    source_url=redact_url(location.url) if location else None,
                    error=_safe_error(exc),
                )
            except Exception as exc:
                technical_error = _safe_error(exc)
                self.repository.finish_attempt(
                    attempt_id,
                    DownloadAttemptStatus.FAILED,
                    source_url=redact_url(location.url) if location else None,
                    error=technical_error,
                )
            finally:
                part_path.unlink(missing_ok=True)

        if technical_error:
            self.repository.mark_item(
                run_id,
                work_id,
                DownloadItemStatus.FAILED,
                landing_url=_redact_optional(work.landing_url),
                error=technical_error,
            )
        else:
            self.repository.mark_item(
                run_id,
                work_id,
                DownloadItemStatus.MANUAL_ACCESS_REQUIRED,
                landing_url=_redact_optional(work.landing_url),
                error="未找到可合法自动获取的开放 PDF",
            )

    def _select_works(
        self, project_id: str, request: DownloadRequest
    ) -> list[WorkView]:
        self._project(project_id)
        works = self.retrieval_repository.all_works(project_id)
        if request.mode == DownloadMode.SELECTED:
            requested = {str(item) for item in request.work_ids or []}
            selected = [work for work in works if str(work.work_id) in requested]
            if {str(work.work_id) for work in selected} != requested:
                raise ValueError("work_ids 包含不属于当前项目的文献")
            return selected
        oa_candidates = [work for work in works if _potentially_open(work)]
        if request.mode == DownloadMode.TOP_N_OA:
            return oa_candidates[: request.top_n]
        return oa_candidates

    def _project(self, project_id: str):
        try:
            return self.project_repository.get_state(project_id)
        except (KeyError, ValueError) as exc:
            raise ProjectNotFoundError(project_id) from exc

    def _output_directories(self, project_id: str) -> tuple[Path, Path]:
        project_root = self.projects_root / project_id
        return project_root / "papers", project_root / "manifest"

    @staticmethod
    def _available_final_path(
        directory: Path, work: WorkView, sha256: str
    ) -> Path:
        stable_id = (
            work.doi
            or work.pmcid
            or work.arxiv_id
            or work.openalex_id
            or str(work.work_id)
        )
        filename = safe_pdf_filename(
            year=work.year,
            first_author=work.authors[0] if work.authors else None,
            title=work.title,
            stable_id=stable_id,
        )
        candidate = directory / filename
        if not candidate.exists():
            return candidate
        return directory / f"{candidate.stem}_{sha256[:8]}.pdf"

    def _write_reports(
        self, run_id: str, manifest_path: Path, failed_path: Path
    ) -> None:
        run = self.repository.get_run(run_id)
        attempts_by_work: dict[UUID, list[dict]] = {}
        for attempt in run.attempts:
            attempts_by_work.setdefault(attempt.work_id, []).append(
                attempt.model_dump(mode="json")
            )

        manifest_temp = manifest_path.with_suffix(".jsonl.part")
        with manifest_temp.open("w", encoding="utf-8", newline="\n") as output:
            for item in run.items:
                payload = item.model_dump(mode="json")
                payload["attempts"] = attempts_by_work.get(item.work_id, [])
                output.write(json.dumps(payload, ensure_ascii=False) + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(manifest_temp, manifest_path)

        failed_temp = failed_path.with_suffix(".csv.part")
        with failed_temp.open("w", encoding="utf-8-sig", newline="") as output:
            writer = csv.DictWriter(
                output,
                fieldnames=["work_id", "title", "status", "landing_url", "error"],
            )
            writer.writeheader()
            for item in run.items:
                if item.status in {
                    DownloadItemStatus.MANUAL_ACCESS_REQUIRED,
                    DownloadItemStatus.FAILED,
                }:
                    writer.writerow(
                        {
                            "work_id": str(item.work_id),
                            "title": item.title,
                            "status": item.status.value,
                            "landing_url": item.landing_url or "",
                            "error": item.error or "",
                        }
                    )
            output.flush()
            os.fsync(output.fileno())
        os.replace(failed_temp, failed_path)


def _potentially_open(work: WorkView) -> bool:
    return bool(
        work.is_oa is True
        or work.pdf_url
        or work.pmcid
        or work.arxiv_id
        or work.doi
    )


def _safe_error(exc: Exception) -> str:
    text = str(exc)
    text = re.sub(
        r"https?://[^\s]+",
        lambda match: redact_url(match.group(0).rstrip(".,);]")),
        text,
    )
    text = re.sub(
        r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        "[REDACTED_EMAIL]",
        text,
    )
    text = re.sub(r"(?i)(api[_-]?key|token)=[^&\s]+", r"\1=***", text)
    return f"{type(exc).__name__}: {text}"[:2000]


def _redact_optional(value: str | None) -> str | None:
    return redact_url(value) if value else None


def _valid_local_file(path_value: str, expected_sha256: str) -> bool:
    path = Path(path_value)
    if not path.is_file():
        return False
    digest = hashlib.sha256()
    header = bytearray()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                if len(header) < 1024:
                    header.extend(chunk[: 1024 - len(header)])
                digest.update(chunk)
    except OSError:
        return False
    return bytes(header).lstrip().startswith(b"%PDF-") and digest.hexdigest() == expected_sha256
