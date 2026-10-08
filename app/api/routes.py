from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.schemas.question import (
    ConfirmQuestionRequest,
    CreateProjectRequest,
    MessageRequest,
    ProjectState,
)
from app.services.projects import (
    ClarificationProjectService,
    InvalidTransitionError,
    ProjectNotFoundError,
)

router = APIRouter(prefix="/projects", tags=["phase-a"])


async def get_service(request: Request) -> ClarificationProjectService:
    return request.app.state.project_service


@router.post("", response_model=ProjectState, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: CreateProjectRequest,
    service: ClarificationProjectService = Depends(get_service),
) -> ProjectState:
    try:
        return service.create_project(payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{project_id}/state", response_model=ProjectState)
async def get_project_state(
    project_id: str,
    service: ClarificationProjectService = Depends(get_service),
) -> ProjectState:
    try:
        return service.get(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="项目不存在") from exc
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{project_id}/messages", response_model=ProjectState)
async def answer_clarification(
    project_id: str,
    payload: MessageRequest,
    service: ClarificationProjectService = Depends(get_service),
) -> ProjectState:
    try:
        return service.answer(project_id, payload)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="项目不存在") from exc
    except (InvalidTransitionError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{project_id}/confirm-question", response_model=ProjectState)
async def confirm_question(
    project_id: str,
    payload: ConfirmQuestionRequest,
    service: ClarificationProjectService = Depends(get_service),
) -> ProjectState:
    try:
        return service.confirm(project_id, payload)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="项目不存在") from exc
    except (InvalidTransitionError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
