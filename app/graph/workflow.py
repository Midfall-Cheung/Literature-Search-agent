from __future__ import annotations

from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.schemas.question import ClarificationQuestion, ProjectStatus, ResearchQuestionSpec
from app.services.clarifier import (
    MAX_CLARIFICATION_ROUNDS,
    HeuristicQuestionAnalyzer,
    QuestionAnalyzer,
    apply_defaults_for_unresolved,
    apply_field_updates,
    build_summary,
    merge_answer,
    missing_fields,
    next_question,
)


class ResearchState(TypedDict, total=False):
    project_id: str
    original_question: str
    question_spec: dict[str, Any]
    clarification_round: int
    missing_fields: list[str]
    current_question: dict[str, Any] | None
    status: str
    summary: str | None
    assumptions: list[str]
    pending_input: dict[str, Any] | None
    confirmation: dict[str, Any] | None


class ClarificationGraph:
    """LangGraph workflow with durable human interrupts."""

    def __init__(self, checkpointer: Any, analyzer: QuestionAnalyzer | None = None):
        self.analyzer = analyzer or HeuristicQuestionAnalyzer()
        self.graph = self._build().compile(checkpointer=checkpointer)

    def _build(self) -> StateGraph:
        builder = StateGraph(ResearchState)
        builder.add_node("analyze_initial_question", self._analyze_initial_question)
        builder.add_node("prepare_next_step", self._prepare_next_step)
        builder.add_node("ask_for_clarification", self._ask_for_clarification)
        builder.add_node("merge_answer", self._merge_answer)
        builder.add_node("ask_for_confirmation", self._ask_for_confirmation)
        builder.add_node("apply_revision", self._apply_revision)
        builder.add_node("mark_confirmed", self._mark_confirmed)

        builder.add_edge(START, "analyze_initial_question")
        builder.add_edge("analyze_initial_question", "prepare_next_step")
        builder.add_conditional_edges(
            "prepare_next_step",
            self._route_prepared_step,
            {
                "clarify": "ask_for_clarification",
                "confirm": "ask_for_confirmation",
            },
        )
        builder.add_edge("ask_for_clarification", "merge_answer")
        builder.add_edge("merge_answer", "prepare_next_step")
        builder.add_conditional_edges(
            "ask_for_confirmation",
            self._route_confirmation,
            {"accepted": "mark_confirmed", "revise": "apply_revision"},
        )
        builder.add_edge("apply_revision", "prepare_next_step")
        builder.add_edge("mark_confirmed", END)
        return builder

    def start(self, project_id: str, original_question: str) -> dict[str, Any]:
        return self.graph.invoke(
            {
                "project_id": project_id,
                "original_question": original_question,
                "clarification_round": 0,
                "status": ProjectStatus.CLARIFYING.value,
                "assumptions": [],
            },
            self.config(project_id),
        )

    def resume(self, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.graph.invoke(Command(resume=payload), self.config(project_id))

    def state(self, project_id: str) -> dict[str, Any]:
        return dict(self.graph.get_state(self.config(project_id)).values)

    @staticmethod
    def config(project_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": project_id}}

    def _analyze_initial_question(self, state: ResearchState) -> ResearchState:
        spec = self.analyzer.analyze(state["original_question"])
        return {"question_spec": spec.model_dump(mode="json")}

    @staticmethod
    def _prepare_next_step(state: ResearchState) -> ResearchState:
        spec = ResearchQuestionSpec.model_validate(state["question_spec"])
        clarification_round = state.get("clarification_round", 0)
        question = next_question(spec, clarification_round)
        assumptions = list(state.get("assumptions", []))

        if question is not None:
            return {
                "question_spec": spec.model_dump(mode="json"),
                "missing_fields": [item.value for item in missing_fields(spec)],
                "current_question": question.model_dump(mode="json"),
                "status": ProjectStatus.CLARIFYING.value,
                "summary": None,
            }

        if clarification_round >= MAX_CLARIFICATION_ROUNDS:
            spec, new_assumptions = apply_defaults_for_unresolved(spec)
            assumptions.extend(item for item in new_assumptions if item not in assumptions)
        return {
            "question_spec": spec.model_dump(mode="json"),
            "missing_fields": [item.value for item in missing_fields(spec)],
            "current_question": None,
            "status": ProjectStatus.AWAITING_CONFIRMATION.value,
            "summary": build_summary(spec, assumptions),
            "assumptions": assumptions,
        }

    @staticmethod
    def _route_prepared_step(state: ResearchState) -> Literal["clarify", "confirm"]:
        return (
            "clarify"
            if state["status"] == ProjectStatus.CLARIFYING.value
            else "confirm"
        )

    @staticmethod
    def _ask_for_clarification(state: ResearchState) -> ResearchState:
        question = ClarificationQuestion.model_validate(state["current_question"])
        answer = interrupt(
            {
                "kind": "clarification",
                "question": question.model_dump(mode="json"),
            }
        )
        return {"pending_input": answer}

    @staticmethod
    def _merge_answer(state: ResearchState) -> ResearchState:
        spec = ResearchQuestionSpec.model_validate(state["question_spec"])
        question = ClarificationQuestion.model_validate(state["current_question"])
        payload = state["pending_input"] or {}
        updated = merge_answer(
            spec,
            question,
            str(payload.get("content", "")),
            payload.get("field_updates"),
        )
        return {
            "question_spec": updated.model_dump(mode="json"),
            "clarification_round": state.get("clarification_round", 0) + 1,
            "pending_input": None,
        }

    @staticmethod
    def _ask_for_confirmation(state: ResearchState) -> ResearchState:
        decision = interrupt(
            {
                "kind": "question_confirmation",
                "summary": state["summary"],
                "question_spec": state["question_spec"],
                "assumptions": state.get("assumptions", []),
            }
        )
        return {"confirmation": decision}

    @staticmethod
    def _route_confirmation(state: ResearchState) -> Literal["accepted", "revise"]:
        return "accepted" if (state.get("confirmation") or {}).get("accepted") else "revise"

    @staticmethod
    def _apply_revision(state: ResearchState) -> ResearchState:
        payload = state.get("confirmation") or {}
        spec = ResearchQuestionSpec.model_validate(state["question_spec"])
        updated = apply_field_updates(spec, payload.get("field_updates") or {})
        return {
            "question_spec": updated.model_dump(mode="json"),
            "status": ProjectStatus.CLARIFYING.value,
            "confirmation": None,
            "assumptions": [],
        }

    @staticmethod
    def _mark_confirmed(state: ResearchState) -> ResearchState:
        spec = ResearchQuestionSpec.model_validate(state["question_spec"])
        confirmed = spec.model_copy(update={"confirmed": True})
        return {
            "question_spec": confirmed.model_dump(mode="json"),
            "status": ProjectStatus.CONFIRMED.value,
            "missing_fields": [],
            "current_question": None,
            "confirmation": None,
            "summary": build_summary(confirmed, state.get("assumptions", [])),
        }

