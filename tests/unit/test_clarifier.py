import pytest

from app.schemas.question import ClarificationQuestion, FieldName, Framework, ResearchQuestionSpec
from app.services.clarifier import (
    HeuristicQuestionAnalyzer,
    StructuredLLMQuestionAnalyzer,
    apply_defaults_for_unresolved,
    apply_field_updates,
    merge_answer,
    missing_fields,
    next_question,
)


class FakeStructuredModel:
    def invoke(self, _: str) -> dict:
        return {
            "original_question": "模型擅自改写的文本",
            "framework": "concept_context_outcome",
            "population_or_object": "研究人员",
            "intervention_or_exposure": "文献检索 Agent",
            "outcomes": ["检索效率"],
            "confirmed": True,
        }


class FakeChatModel:
    def with_structured_output(self, _: type) -> FakeStructuredModel:
        return FakeStructuredModel()


def test_framework_selection() -> None:
    analyzer = HeuristicQuestionAnalyzer()
    assert analyzer.analyze("糖尿病患者的治疗效果").framework == Framework.PICO
    assert analyzer.analyze("空气污染暴露的风险因素研究").framework == Framework.PECO
    assert analyzer.analyze("护士工作体验的质性访谈").framework == Framework.SPIDER
    assert (
        analyzer.analyze("大语言模型在文献检索中的应用").framework
        == Framework.CONCEPT_CONTEXT_OUTCOME
    )


def test_structured_llm_adapter_preserves_original_and_never_auto_confirms() -> None:
    original = "文献检索 Agent 能否提高研究人员的检索效率？"
    spec = StructuredLLMQuestionAnalyzer(FakeChatModel()).analyze(original)
    assert spec.original_question == original
    assert spec.population_or_object == "研究人员"
    assert spec.confirmed is False


def test_extracts_only_high_confidence_labelled_fields_and_boundaries() -> None:
    spec = HeuristicQuestionAnalyzer().analyze(
        "对象：高校教师；核心概念：生成式人工智能；结果：效率、准确率；"
        "系统综述，2020-2026，中英文"
    )
    assert spec.population_or_object == "高校教师"
    assert spec.intervention_or_exposure == "生成式人工智能"
    assert spec.outcomes == ["效率", "准确率"]
    assert spec.study_types == ["systematic_review"]
    assert (spec.date_from, spec.date_to) == (2020, 2026)
    assert spec.languages == ["zh", "en"]


def test_priority_asks_population_first() -> None:
    spec = ResearchQuestionSpec(original_question="研究人工智能")
    question = next_question(spec, clarification_round=0)
    assert question is not None
    assert question.target_fields == [FieldName.POPULATION_OR_OBJECT]


def test_new_answer_does_not_overwrite_existing_fields() -> None:
    spec = ResearchQuestionSpec(
        original_question="研究人工智能",
        population_or_object="高校教师",
    )
    question = ClarificationQuestion(
        prompt="核心概念是什么？",
        target_fields=[FieldName.INTERVENTION_OR_EXPOSURE],
        round_number=1,
    )
    updated = merge_answer(spec, question, "生成式人工智能")
    assert updated.population_or_object == "高校教师"
    assert updated.intervention_or_exposure == "生成式人工智能"


def test_unrestricted_answer_resolves_boundary_without_fake_value() -> None:
    spec = ResearchQuestionSpec(
        original_question="研究人工智能",
        population_or_object="教师",
        intervention_or_exposure="人工智能",
        outcomes=["效率"],
    )
    question = ClarificationQuestion(
        prompt="时间？",
        target_fields=[FieldName.DATE_FROM, FieldName.DATE_TO],
        round_number=1,
    )
    updated = merge_answer(spec, question, "不限")
    assert updated.date_from is None
    assert updated.date_to is None
    assert FieldName.DATE_FROM in updated.explicitly_unrestricted
    assert FieldName.DATE_FROM not in missing_fields(updated)


def test_invalid_date_range_is_rejected() -> None:
    with pytest.raises(ValueError, match="date_from"):
        apply_field_updates(
            ResearchQuestionSpec(original_question="研究人工智能"),
            {"date_from": 2026, "date_to": 2020},
        )


def test_unknown_field_update_is_rejected() -> None:
    with pytest.raises(ValueError, match="不允许更新字段"):
        apply_field_updates(
            ResearchQuestionSpec(original_question="研究人工智能"),
            {"admin": True},
        )


def test_defaults_after_round_limit_are_auditable() -> None:
    spec, assumptions = apply_defaults_for_unresolved(
        ResearchQuestionSpec(original_question="研究人工智能")
    )
    assert spec.population_or_object == "研究人工智能"
    assert FieldName.LANGUAGES in spec.explicitly_unrestricted
    assert assumptions
    assert not missing_fields(spec)
