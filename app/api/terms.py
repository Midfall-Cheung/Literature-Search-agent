from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.repositories.terms import TermVersionConflictError
from app.schemas.terms import (
    ConfirmTermsRequest,
    GenerateTermsRequest,
    QueryPreview,
    ReplaceTermsRequest,
    TermTable,
    TermHistory,
)
from app.services.projects import InvalidTransitionError, ProjectNotFoundError
from app.services.terms import TermSetAlreadyExistsError, TermTableService

router = APIRouter(prefix="/projects", tags=["phase-b"])


async def get_term_service(request: Request) -> TermTableService:
    return request.app.state.term_service


def translate_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ProjectNotFoundError):
        return HTTPException(status_code=404, detail="项目不存在")
    if isinstance(exc, (TermVersionConflictError, TermSetAlreadyExistsError)):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


@router.post(
    "/{project_id}/terms/generate",
    response_model=TermTable,
    status_code=status.HTTP_201_CREATED,
)
async def generate_terms(
    project_id: str,
    payload: GenerateTermsRequest,
    service: TermTableService = Depends(get_term_service),
) -> TermTable:
    try:
        return service.generate(project_id, payload)
    except (ProjectNotFoundError, InvalidTransitionError, ValueError) as exc:
        raise translate_error(exc) from exc


@router.get("/{project_id}/terms", response_model=TermTable)
async def get_terms(
    project_id: str,
    service: TermTableService = Depends(get_term_service),
) -> TermTable:
    try:
        return service.get(project_id)
    except (ProjectNotFoundError, ValueError) as exc:
        raise translate_error(exc) from exc


@router.get("/{project_id}/terms/history", response_model=TermHistory)
async def get_term_history(
    project_id: str,
    service: TermTableService = Depends(get_term_service),
) -> TermHistory:
    try:
        return service.history(project_id)
    except (ProjectNotFoundError, ValueError) as exc:
        raise translate_error(exc) from exc


@router.put("/{project_id}/terms", response_model=TermTable)
async def replace_terms(
    project_id: str,
    payload: ReplaceTermsRequest,
    service: TermTableService = Depends(get_term_service),
) -> TermTable:
    try:
        return service.replace(project_id, payload)
    except (ProjectNotFoundError, InvalidTransitionError, ValueError) as exc:
        raise translate_error(exc) from exc


@router.post("/{project_id}/confirm-terms", response_model=TermTable)
async def confirm_terms(
    project_id: str,
    payload: ConfirmTermsRequest,
    service: TermTableService = Depends(get_term_service),
) -> TermTable:
    try:
        return service.confirm(project_id, payload)
    except (ProjectNotFoundError, InvalidTransitionError, ValueError) as exc:
        raise translate_error(exc) from exc


@router.get("/{project_id}/query-preview", response_model=QueryPreview)
async def query_preview(
    project_id: str,
    service: TermTableService = Depends(get_term_service),
) -> QueryPreview:
    try:
        return service.preview(project_id)
    except (ProjectNotFoundError, InvalidTransitionError, ValueError) as exc:
        raise translate_error(exc) from exc


@router.get("/{project_id}/terms/export.csv")
async def export_terms_csv(
    project_id: str,
    service: TermTableService = Depends(get_term_service),
) -> Response:
    try:
        filename, content = service.export_csv(project_id)
    except (ProjectNotFoundError, InvalidTransitionError, ValueError) as exc:
        raise translate_error(exc) from exc
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
