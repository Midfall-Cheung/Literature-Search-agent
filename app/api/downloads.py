from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.schemas.downloads import DownloadEstimate, DownloadRequest, DownloadRun
from app.services.downloads import DownloadRunNotFoundError, DownloadService
from app.services.projects import InvalidTransitionError, ProjectNotFoundError


router = APIRouter(prefix="/projects", tags=["phase-e"])


async def get_service(request: Request) -> DownloadService:
    return request.app.state.download_service


def translate_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ProjectNotFoundError):
        return HTTPException(status_code=404, detail="项目不存在")
    if isinstance(exc, DownloadRunNotFoundError):
        return HTTPException(status_code=404, detail="下载运行不存在")
    if isinstance(exc, InvalidTransitionError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


@router.post("/{project_id}/downloads/estimate", response_model=DownloadEstimate)
async def estimate_downloads(
    project_id: str,
    payload: DownloadRequest,
    service: DownloadService = Depends(get_service),
) -> DownloadEstimate:
    try:
        return service.estimate(project_id, payload)
    except (ProjectNotFoundError, InvalidTransitionError, ValueError) as exc:
        raise translate_error(exc) from exc


@router.post(
    "/{project_id}/downloads",
    response_model=DownloadRun,
    status_code=status.HTTP_201_CREATED,
)
async def start_downloads(
    project_id: str,
    payload: DownloadRequest,
    service: DownloadService = Depends(get_service),
) -> DownloadRun:
    try:
        return await service.start(project_id, payload)
    except (ProjectNotFoundError, InvalidTransitionError, ValueError) as exc:
        raise translate_error(exc) from exc


@router.get("/{project_id}/downloads/{run_id}", response_model=DownloadRun)
async def get_download_run(
    project_id: str,
    run_id: str,
    service: DownloadService = Depends(get_service),
) -> DownloadRun:
    try:
        return service.get(project_id, run_id)
    except (ProjectNotFoundError, DownloadRunNotFoundError, ValueError) as exc:
        raise translate_error(exc) from exc


@router.post(
    "/{project_id}/downloads/{run_id}/resume", response_model=DownloadRun
)
async def resume_download_run(
    project_id: str,
    run_id: str,
    service: DownloadService = Depends(get_service),
) -> DownloadRun:
    try:
        return await service.resume(project_id, run_id)
    except (
        ProjectNotFoundError,
        DownloadRunNotFoundError,
        InvalidTransitionError,
        ValueError,
    ) as exc:
        raise translate_error(exc) from exc


@router.get("/{project_id}/downloads/{run_id}/manifest.jsonl")
async def download_manifest(
    project_id: str,
    run_id: str,
    service: DownloadService = Depends(get_service),
) -> Response:
    return _report_response(service, project_id, run_id, "manifest")


@router.get("/{project_id}/downloads/{run_id}/failed.csv")
async def failed_downloads_report(
    project_id: str,
    run_id: str,
    service: DownloadService = Depends(get_service),
) -> Response:
    return _report_response(service, project_id, run_id, "failed")


def _report_response(
    service: DownloadService, project_id: str, run_id: str, kind: str
) -> Response:
    try:
        filename, content, media_type = service.report(project_id, run_id, kind)
    except (
        ProjectNotFoundError,
        DownloadRunNotFoundError,
        InvalidTransitionError,
        ValueError,
    ) as exc:
        raise translate_error(exc) from exc
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

