from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.repositories.query_plans import QueryPlanVersionConflictError
from app.schemas.query_plans import (
    CompileQueryPlanRequest,
    ConfirmQueryPlanRequest,
    QueryPlan,
    QueryPlanHistory,
)
from app.services.projects import InvalidTransitionError, ProjectNotFoundError
from app.services.query_plans import QueryPlanAlreadyExistsError, QueryPlanService


router = APIRouter(prefix="/projects", tags=["phase-c"])


async def get_service(request: Request) -> QueryPlanService:
    return request.app.state.query_plan_service


def translate_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ProjectNotFoundError):
        return HTTPException(status_code=404, detail="项目不存在")
    if isinstance(
        exc, (QueryPlanVersionConflictError, QueryPlanAlreadyExistsError)
    ):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


@router.post(
    "/{project_id}/query-plans/compile",
    response_model=QueryPlan,
    status_code=status.HTTP_201_CREATED,
)
async def compile_query_plan(
    project_id: str,
    payload: CompileQueryPlanRequest,
    service: QueryPlanService = Depends(get_service),
) -> QueryPlan:
    try:
        return await service.compile(project_id, payload)
    except (
        ProjectNotFoundError,
        InvalidTransitionError,
        QueryPlanAlreadyExistsError,
        QueryPlanVersionConflictError,
        ValueError,
    ) as exc:
        raise translate_error(exc) from exc


@router.get("/{project_id}/query-plans", response_model=QueryPlan)
async def get_query_plan(
    project_id: str, service: QueryPlanService = Depends(get_service)
) -> QueryPlan:
    try:
        return service.get(project_id)
    except (ProjectNotFoundError, ValueError) as exc:
        raise translate_error(exc) from exc


@router.get("/{project_id}/query-plans/history", response_model=QueryPlanHistory)
async def get_query_plan_history(
    project_id: str, service: QueryPlanService = Depends(get_service)
) -> QueryPlanHistory:
    try:
        return service.history(project_id)
    except (ProjectNotFoundError, ValueError) as exc:
        raise translate_error(exc) from exc


@router.post("/{project_id}/confirm-query-plan", response_model=QueryPlan)
async def confirm_query_plan(
    project_id: str,
    payload: ConfirmQueryPlanRequest,
    service: QueryPlanService = Depends(get_service),
) -> QueryPlan:
    try:
        return service.confirm(project_id, payload)
    except (
        ProjectNotFoundError,
        InvalidTransitionError,
        QueryPlanVersionConflictError,
        ValueError,
    ) as exc:
        raise translate_error(exc) from exc
