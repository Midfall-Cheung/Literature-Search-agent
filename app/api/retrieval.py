from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from app.schemas.query_plans import ProviderName
from app.schemas.retrieval import SearchRun, SearchRunRequest, WorkPage
from app.services.projects import InvalidTransitionError, ProjectNotFoundError
from app.services.retrieval import RetrievalService, SearchRunNotFoundError


router = APIRouter(prefix="/projects", tags=["phase-d"])


async def get_service(request: Request) -> RetrievalService:
    return request.app.state.retrieval_service


def translate_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ProjectNotFoundError):
        return HTTPException(status_code=404, detail="项目不存在")
    if isinstance(exc, SearchRunNotFoundError):
        return HTTPException(status_code=404, detail="检索运行不存在")
    if isinstance(exc, InvalidTransitionError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


@router.post(
    "/{project_id}/search-runs",
    response_model=SearchRun,
    status_code=status.HTTP_201_CREATED,
)
async def start_search_run(
    project_id: str,
    payload: SearchRunRequest,
    service: RetrievalService = Depends(get_service),
) -> SearchRun:
    try:
        return await service.start(project_id, payload)
    except (ProjectNotFoundError, InvalidTransitionError, ValueError) as exc:
        raise translate_error(exc) from exc


@router.get("/{project_id}/search-runs/{run_id}", response_model=SearchRun)
async def get_search_run(
    project_id: str,
    run_id: str,
    service: RetrievalService = Depends(get_service),
) -> SearchRun:
    try:
        return service.get_run(project_id, run_id)
    except (ProjectNotFoundError, SearchRunNotFoundError, ValueError) as exc:
        raise translate_error(exc) from exc


@router.post(
    "/{project_id}/search-runs/{run_id}/resume", response_model=SearchRun
)
async def resume_search_run(
    project_id: str,
    run_id: str,
    service: RetrievalService = Depends(get_service),
) -> SearchRun:
    try:
        return await service.resume(project_id, run_id)
    except (
        ProjectNotFoundError,
        SearchRunNotFoundError,
        InvalidTransitionError,
        ValueError,
    ) as exc:
        raise translate_error(exc) from exc


@router.get("/{project_id}/works", response_model=WorkPage)
async def list_works(
    project_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
    source: ProviderName | None = None,
    is_oa: bool | None = None,
    service: RetrievalService = Depends(get_service),
) -> WorkPage:
    try:
        return service.works(
            project_id,
            offset=offset,
            limit=limit,
            source=source,
            is_oa=is_oa,
        )
    except (ProjectNotFoundError, ValueError) as exc:
        raise translate_error(exc) from exc


@router.get("/{project_id}/exports/works.csv")
async def export_works_csv(
    project_id: str,
    service: RetrievalService = Depends(get_service),
) -> Response:
    try:
        filename, content = service.export_csv(project_id)
    except (ProjectNotFoundError, ValueError) as exc:
        raise translate_error(exc) from exc
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
