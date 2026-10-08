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
    parse_answer_for_targets,
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


@pytest.mark.parametrize('answer,expected', [
    ('中文', ['zh']), ('英文', ['en']), ('中英文', ['zh', 'en']),
    ('中文和英文', ['zh', 'en']), ('英文和中文', ['en', 'zh']),
    ('中文、英文', ['zh', 'en']), ('zh,en', ['zh', 'en']),
    ('zh / en', ['zh', 'en']), ('ja,fr,de,ja', ['ja', 'fr', 'de']),
])
def test_language_answers(answer, expected):
    assert parse_answer_for_targets(answer, [FieldName.LANGUAGES]) == {'languages': expected}


@pytest.mark.parametrize('answer', ['[2025,2026]', '2025,2026', '2025-2026', 'abc,def', '[]', '中文,abc'])
def test_invalid_language_answers(answer):
    with pytest.raises(ValueError, match='语言'):
        parse_answer_for_targets(answer, [FieldName.LANGUAGES])


@pytest.mark.parametrize('value', [['2025', '2026'], ['zh', 'oops'], [''], 'zh', [12]])
def test_model_rejects_invalid_languages(value):
    with pytest.raises(ValueError):
        ResearchQuestionSpec(original_question='研究人工智能', languages=value)


def test_language_normalization_and_no_date_crosstalk():
    spec = ResearchQuestionSpec(original_question='研究人工智能', date_from=2025, date_to=2026)
    question = ClarificationQuestion(prompt='语言', target_fields=[FieldName.LANGUAGES], round_number=1)
    updated = merge_answer(spec, question, '中英文')
    assert (updated.date_from, updated.date_to) == (2025, 2026)
    assert ResearchQuestionSpec(original_question='研究人工智能', languages=['EN', 'en', 'zh']).languages == ['en', 'zh']


@pytest.mark.parametrize('answer,start,end,unrestricted', [
    ('2020-2026', 2020, 2026, []), ('2020—2026', 2020, 2026, []),
    ('2020 至 2026', 2020, 2026, []), ('2020年到2026年', 2020, 2026, []),
    ('2020年以来', 2020, None, ['date_to']), ('截至2026年', None, 2026, ['date_from']),
    ('不限', None, None, ['date_from', 'date_to']), ('2026,2020', 2020, 2026, []),
])
def test_date_answers(answer, start, end, unrestricted):
    spec = ResearchQuestionSpec(original_question='研究人工智能', languages=['zh'])
    updates = parse_answer_for_targets(answer, [FieldName.DATE_FROM, FieldName.DATE_TO])
    updated = apply_field_updates(spec, updates)
    assert (updated.date_from, updated.date_to) == (start, end)
    assert set(updated.explicitly_unrestricted) == set(unrestricted)
    assert updated.languages == ['zh']


@pytest.mark.parametrize('answer', ['2026-2020', '2025,2026,2027', '2025-abc', '无年份', '1499-2026', '2020-2101'])
def test_invalid_date_answers(answer):
    with pytest.raises(ValueError):
        parse_answer_for_targets(answer, [FieldName.DATE_FROM, FieldName.DATE_TO])


def test_unrestricted_clears_values_and_concrete_updates_clear_flags():
    spec = ResearchQuestionSpec(original_question='研究人工智能', languages=['zh'], date_from=2020, date_to=2026, outcomes=['效率'])
    updated = apply_field_updates(spec, {'explicitly_unrestricted': ['languages', 'date_from', 'date_to']})
    assert updated.languages == []
    assert updated.date_from is updated.date_to is None
    updated = apply_field_updates(updated, {'languages': ['en'], 'date_from': 2021, 'date_to': 2025})
    assert updated.explicitly_unrestricted == []
    assert updated.outcomes == ['效率']


@pytest.mark.parametrize('updates', [
    {'languages': ['abc']}, {'date_from': '2020'}, {'date_from': True},
    {'outcomes': '效率'}, {'outcomes': [1]}, {'population_or_object': 12},
    {'explicitly_unrestricted': 'languages'}, {'explicitly_unrestricted': ['unknown']},
    {'explicitly_unrestricted': ['languages'], 'languages': ['zh']},
    {'confirmed': True},
])
def test_invalid_updates_are_atomic(updates):
    spec = ResearchQuestionSpec(original_question='研究人工智能', languages=['en'])
    before = spec.model_dump()
    with pytest.raises(ValueError):
        apply_field_updates(spec, updates)
    assert spec.model_dump() == before


@pytest.mark.parametrize('text', ['2026-2020', '2025,2026,2027', '1499-2026'])
def test_initial_dates_use_same_validation(text):
    with pytest.raises(ValueError):
        HeuristicQuestionAnalyzer().analyze('研究人工智能，' + text)


def test_llm_model_instance_cannot_bypass_validation():
    class Model:
        def with_structured_output(self, schema):
            return self
        def invoke(self, prompt):
            return ResearchQuestionSpec.model_construct(original_question='伪造', languages=['2025'], confirmed=True)
    with pytest.raises(ValueError):
        StructuredLLMQuestionAnalyzer(Model()).analyze('研究人工智能')


@pytest.mark.parametrize('answer', ['不限', '不限制'])
def test_language_unrestricted_answer(answer):
    updated = apply_field_updates(
        ResearchQuestionSpec(original_question='研究人工智能', languages=['en']),
        parse_answer_for_targets(answer, [FieldName.LANGUAGES]),
    )
    assert updated.languages == []
    assert updated.explicitly_unrestricted == [FieldName.LANGUAGES]


@pytest.mark.parametrize('value', [None, 123, {}, ['languages', None]])
def test_invalid_unrestricted_types(value):
    with pytest.raises(ValueError):
        apply_field_updates(ResearchQuestionSpec(original_question='研究人工智能'), {'explicitly_unrestricted': value})


def test_llm_dict_cannot_save_invalid_languages_or_conflicting_unrestricted():
    class Model:
        def __init__(self, payload):
            self.payload = payload
        def with_structured_output(self, schema):
            return self
        def invoke(self, prompt):
            return dict(original_question='模型文本', confirmed=True, **self.payload)
    for payload in ({'languages': ['2025']}, {'languages': ['en'], 'explicitly_unrestricted': ['languages']}):
        with pytest.raises(ValueError):
            StructuredLLMQuestionAnalyzer(Model(payload)).analyze('研究人工智能')


def test_one_sided_date_answer_clears_old_value_and_markers():
    spec = ResearchQuestionSpec(original_question='研究人工智能', date_from=2020, date_to=2026)
    updated = apply_field_updates(spec, parse_answer_for_targets('截至2025年', [FieldName.DATE_FROM, FieldName.DATE_TO]))
    assert (updated.date_from, updated.date_to) == (None, 2025)
    updated = apply_field_updates(updated, parse_answer_for_targets('2021年以来', [FieldName.DATE_FROM, FieldName.DATE_TO]))
    assert (updated.date_from, updated.date_to) == (2021, None)
    assert updated.explicitly_unrestricted == [FieldName.DATE_TO]


@pytest.mark.parametrize('answer,expected', [
    ('只要中文，不纳入英文文献', ['zh']),
    ('我想检索中文和英文文献', ['zh', 'en']),
    ('不纳入英文和法文，只要中文', ['zh']),
    ('不纳入英文、法文，只要中文', ['zh']),
    ('中文，但不要英文', ['zh']),
    ('英文文献不要，只要中文', ['zh']),
])
def test_natural_language_and_negated_languages(answer, expected):
    assert parse_answer_for_targets(answer, [FieldName.LANGUAGES]) == {'languages': expected}
    spec = HeuristicQuestionAnalyzer().analyze('研究人工智能；' + answer)
    assert spec.languages == expected


@pytest.mark.parametrize('answer', ['[2025,2026]', '中文,abc', '我想检索中文和abc文献', '不纳入英文文献'])
def test_natural_language_parser_rejects_invalid_or_exclusion_only(answer):
    with pytest.raises(ValueError, match='语言'):
        parse_answer_for_targets(answer, [FieldName.LANGUAGES])
