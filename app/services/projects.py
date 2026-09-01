from __future__ import annotations

from app.graph.workflow import ClarificationGraph
from app.repositories.projects import ProjectRepository
from app.schemas.question import (
    ConfirmQuestionRequest,
    CreateProjectRequest,
    MessageRequest,
    ProjectState,
    ProjectStatus,
)


class ProjectNotFoundError(KeyError):
    pass


class InvalidTransitionError(ValueError):
    pass


class ClarificationProjectService:
    def __init__(self, repository: ProjectRepository, graph: ClarificationGraph):
        self.repository = repository
        self.graph = graph

    def create_project(self, request: CreateProjectRequest) -> ProjectState:
        project_id = self.repository.create(request.original_question)
        self.graph.start(project_id, request.original_question)
        self.repository.sync_graph_state(project_id, self.graph.state(project_id))
        return self.repository.get_state(project_id)

    def answer(self, project_id: str, request: MessageRequest) -> ProjectState:
        state = self._get(project_id)
        if state.status != ProjectStatus.CLARIFYING or state.current_question is None:
            raise InvalidTransitionError("当前项目不在澄清问答状态")
        target_fields = [field.value for field in state.current_question.target_fields]
        self.graph.resume(
            project_id,
            {"content": request.content, "field_updates": request.field_updates},
        )
        self.repository.add_user_message(project_id, request.content, target_fields)
        self.repository.sync_graph_state(project_id, self.graph.state(project_id))
        return self.repository.get_state(project_id)

    def confirm(self, project_id: str, request: ConfirmQuestionRequest) -> ProjectState:
        state = self._get(project_id)
        if state.status != ProjectStatus.AWAITING_CONFIRMATION:
            raise InvalidTransitionError("当前项目尚未进入问题确认状态")
        self.graph.resume(
            project_id,
            {
                "accepted": request.accepted,
                "feedback": request.feedback,
                "field_updates": request.field_updates,
            },
        )
        self.repository.add_confirmation_message(
            project_id, request.accepted, request.feedback
        )
        self.repository.sync_graph_state(project_id, self.graph.state(project_id))
        return self.repository.get_state(project_id)

    def get(self, project_id: str) -> ProjectState:
        return self._get(project_id)

    def _get(self, project_id: str) -> ProjectState:
        try:
            return self.repository.get_state(project_id)
        except (KeyError, ValueError) as exc:
            raise ProjectNotFoundError(project_id) from exc

