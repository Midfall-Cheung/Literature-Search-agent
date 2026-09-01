from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Protocol

from app.schemas.question import (
    ClarificationQuestion,
    FieldName,
    Framework,
    ResearchQuestionSpec,
)

MAX_CLARIFICATION_ROUNDS = 6

CORE_FIELDS = (
    FieldName.POPULATION_OR_OBJECT,
    FieldName.INTERVENTION_OR_EXPOSURE,
    FieldName.OUTCOMES,
)
BOUNDARY_FIELDS = (
    FieldName.STUDY_TYPES,
    FieldName.DATE_FROM,
    FieldName.DATE_TO,
    FieldName.LANGUAGES,
)
UPDATEABLE_FIELDS = {item.value for item in FieldName}


class QuestionAnalyzer(Protocol):
    """Replaceable boundary for an LLM with structured output."""

    def analyze(self, original_question: str) -> ResearchQuestionSpec: ...


class StructuredLLMQuestionAnalyzer:
    """Provider-neutral adapter for chat models supporting structured output."""

    def __init__(self, chat_model: Any):
        self.structured_model = chat_model.with_structured_output(ResearchQuestionSpec)

    def analyze(self, original_question: str) -> ResearchQuestionSpec:
        prompt = f"""你是文献检索问题分析器。只抽取用户已经明确表达的信息，不要猜测。
根据问题选择 PICO、PECO、SPIDER 或 concept_context_outcome 框架。
没有说明的字段保留 null 或空列表；不要把缺失解释成“不限”。
original_question 必须原样保留，confirmed 必须为 false。

用户问题：
{original_question}
"""
        result = self.structured_model.invoke(prompt)
        parsed = (
            result
            if isinstance(result, ResearchQuestionSpec)
            else ResearchQuestionSpec.model_validate(result)
        )
        return parsed.model_copy(
            update={"original_question": original_question, "confirmed": False}
        )


class HeuristicQuestionAnalyzer:
    """Deterministic fallback used locally and in tests.

    It deliberately extracts only high-confidence signals. A production LLM adapter can
    implement ``QuestionAnalyzer`` without changing the workflow or API.
    """

    def analyze(self, original_question: str) -> ResearchQuestionSpec:
        framework = infer_framework(original_question)
        spec = ResearchQuestionSpec(
            original_question=original_question.strip(),
            framework=framework,
            research_objective=infer_objective(original_question),
        )
        updates = extract_labelled_fields(original_question)
        updates.update(extract_boundaries(original_question))
        return apply_field_updates(spec, updates)


def infer_framework(question: str) -> Framework:
    lowered = question.casefold()
    if any(
        token in lowered
        for token in ("质性", "访谈", "体验", "感受", "qualitative", "interview")
    ):
        return Framework.SPIDER
    if any(
        token in lowered
        for token in ("暴露", "风险因素", "危险因素", "risk factor", "exposure")
    ):
        return Framework.PECO
    if any(
        token in lowered
        for token in (
            "临床",
            "治疗",
            "疗效",
            "患者",
            "随机对照",
            "clinical",
            "therapy",
            "patient",
        )
    ):
        return Framework.PICO
    return Framework.CONCEPT_CONTEXT_OUTCOME


def infer_objective(question: str) -> str:
    lowered = question.casefold()
    if any(token in lowered for token in ("比较", "对比", "versus", " vs ")):
        return "比较"
    if any(token in lowered for token in ("影响", "效果", "疗效", "effect", "impact")):
        return "评估"
    if any(token in lowered for token in ("解释", "机制", "mechanism")):
        return "解释"
    return "综述"


def extract_labelled_fields(text: str) -> dict[str, Any]:
    aliases: dict[str, str] = {
        "人群": "population_or_object",
        "对象": "population_or_object",
        "population": "population_or_object",
        "干预": "intervention_or_exposure",
        "暴露": "intervention_or_exposure",
        "核心概念": "intervention_or_exposure",
        "intervention": "intervention_or_exposure",
        "exposure": "intervention_or_exposure",
        "结果": "outcomes",
        "结局": "outcomes",
        "outcome": "outcomes",
        "场景": "context",
        "context": "context",
    }
    updates: dict[str, Any] = {}
    segments = re.split(r"[;；\n]", text)
    for segment in segments:
        match = re.match(r"\s*([^:：]{1,20})\s*[:：]\s*(.+?)\s*$", segment)
        if not match:
            continue
        key = aliases.get(match.group(1).strip().casefold())
        if not key:
            continue
        value = match.group(2).strip()
        updates[key] = split_values(value) if key == "outcomes" else value
    return updates


def extract_boundaries(text: str) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    years = [int(value) for value in re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", text)]
    if len(years) >= 2:
        updates["date_from"], updates["date_to"] = min(years), max(years)
    elif len(years) == 1 and any(token in text for token in ("以来", "之后", "起")):
        updates["date_from"] = years[0]
        updates["date_to"] = None

    languages: list[str] = []
    lowered = text.casefold()
    if "中文" in text or "中英" in text or "chinese" in lowered:
        languages.append("zh")
    if "英文" in text or "中英" in text or "english" in lowered:
        languages.append("en")
    if languages:
        updates["languages"] = languages

    study_type_tokens = {
        "随机对照": "randomized_controlled_trial",
        "系统综述": "systematic_review",
        "meta分析": "meta_analysis",
        "meta-analysis": "meta_analysis",
        "队列研究": "cohort_study",
        "质性研究": "qualitative_study",
    }
    study_types = [value for token, value in study_type_tokens.items() if token in lowered]
    if study_types:
        updates["study_types"] = study_types
    if any(token in lowered for token in ("开放全文", "open access only", "oa全文")):
        updates["fulltext_requirement"] = "open_fulltext_only"
    return updates


def missing_fields(spec: ResearchQuestionSpec) -> list[FieldName]:
    unrestricted = set(spec.explicitly_unrestricted)
    result: list[FieldName] = []
    for field in (*CORE_FIELDS, *BOUNDARY_FIELDS):
        if field in unrestricted:
            continue
        value = getattr(spec, field.value)
        if value is None or value == [] or value == "":
            result.append(field)
    return result


def next_question(
    spec: ResearchQuestionSpec, clarification_round: int
) -> ClarificationQuestion | None:
    missing = missing_fields(spec)
    if not missing or clarification_round >= MAX_CLARIFICATION_ROUNDS:
        return None

    next_round = clarification_round + 1
    if FieldName.POPULATION_OR_OBJECT in missing:
        wording = {
            Framework.PICO: "你关注的是哪类患者或人群？请尽量说明疾病、年龄或其他关键特征。",
            Framework.PECO: "你关注的是哪类人群或对象？请说明决定范围的关键特征。",
            Framework.SPIDER: "这项质性问题关注哪些参与者或样本？",
            Framework.CONCEPT_CONTEXT_OUTCOME: "这项研究具体关注哪类人群、对象或技术？",
        }[spec.framework]
        return ClarificationQuestion(
            prompt=wording,
            target_fields=[FieldName.POPULATION_OR_OBJECT],
            round_number=next_round,
        )
    if FieldName.INTERVENTION_OR_EXPOSURE in missing:
        wording = {
            Framework.PICO: "需要评估的具体干预、治疗或诊断措施是什么？",
            Framework.PECO: "需要研究的暴露或风险因素具体是什么？",
            Framework.SPIDER: "你想研究的现象、体验或核心概念是什么？",
            Framework.CONCEPT_CONTEXT_OUTCOME: "检索必须围绕的核心技术、现象或概念是什么？",
        }[spec.framework]
        return ClarificationQuestion(
            prompt=wording,
            target_fields=[FieldName.INTERVENTION_OR_EXPOSURE],
            round_number=next_round,
        )
    if FieldName.OUTCOMES in missing:
        return ClarificationQuestion(
            prompt="你最关心哪些结果或结局？多个结果可用逗号分隔。",
            target_fields=[FieldName.OUTCOMES],
            round_number=next_round,
        )
    if FieldName.STUDY_TYPES in missing:
        return ClarificationQuestion(
            prompt="希望纳入哪些研究设计或文献类型？如果不限制，请回答“不限”。",
            target_fields=[FieldName.STUDY_TYPES],
            round_number=next_round,
        )
    if FieldName.DATE_FROM in missing or FieldName.DATE_TO in missing:
        return ClarificationQuestion(
            prompt="文献发表时间范围是什么？例如“2020—2026”；不限制可回答“不限”。",
            target_fields=[FieldName.DATE_FROM, FieldName.DATE_TO],
            round_number=next_round,
        )
    if FieldName.LANGUAGES in missing:
        return ClarificationQuestion(
            prompt="希望纳入哪些语言的文献？例如“中文和英文”；不限制可回答“不限”。",
            target_fields=[FieldName.LANGUAGES],
            round_number=next_round,
        )
    return None


def merge_answer(
    spec: ResearchQuestionSpec,
    question: ClarificationQuestion,
    content: str,
    explicit_updates: Mapping[str, Any] | None = None,
) -> ResearchQuestionSpec:
    updates = dict(explicit_updates or {})
    if not updates:
        updates = parse_answer_for_targets(content, question.target_fields)
    return apply_field_updates(spec, updates)


def parse_answer_for_targets(content: str, targets: list[FieldName]) -> dict[str, Any]:
    value = content.strip()
    unrestricted = value.casefold() in {
        "不限",
        "不限制",
        "无限制",
        "any",
        "no restriction",
    }
    if unrestricted:
        return {"explicitly_unrestricted": [field.value for field in targets]}

    if targets == [FieldName.OUTCOMES]:
        return {"outcomes": split_values(value)}
    if targets == [FieldName.STUDY_TYPES]:
        return {"study_types": split_values(value)}
    if targets == [FieldName.LANGUAGES]:
        detected = extract_boundaries(value).get("languages")
        return {"languages": detected or split_values(value)}
    if set(targets) == {FieldName.DATE_FROM, FieldName.DATE_TO}:
        years = [int(item) for item in re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", value)]
        if len(years) >= 2:
            return {"date_from": min(years), "date_to": max(years)}
        if len(years) == 1:
            if any(token in value for token in ("以前", "之前", "截至")):
                return {"date_to": years[0], "explicitly_unrestricted": ["date_from"]}
            return {"date_from": years[0], "explicitly_unrestricted": ["date_to"]}
        raise ValueError("未识别到有效年份，请使用如“2020—2026”的格式")
    if len(targets) == 1:
        return {targets[0].value: value}
    raise ValueError("无法把回答映射到当前问题")


def apply_field_updates(
    spec: ResearchQuestionSpec, updates: Mapping[str, Any]
) -> ResearchQuestionSpec:
    unknown = set(updates) - UPDATEABLE_FIELDS - {"explicitly_unrestricted"}
    if unknown:
        raise ValueError(f"不允许更新字段: {', '.join(sorted(unknown))}")

    data = spec.model_dump(mode="python")
    unrestricted = set(data.get("explicitly_unrestricted", []))
    for key, value in updates.items():
        if key == "explicitly_unrestricted":
            unrestricted.update(FieldName(item) for item in value)
            continue
        data[key] = value
        unrestricted.discard(FieldName(key))
    data["explicitly_unrestricted"] = list(unrestricted)
    data["confirmed"] = False
    return ResearchQuestionSpec.model_validate(data)


def apply_defaults_for_unresolved(
    spec: ResearchQuestionSpec,
) -> tuple[ResearchQuestionSpec, list[str]]:
    unresolved = missing_fields(spec)
    if not unresolved:
        return spec, []
    data = spec.model_dump(mode="python")
    unrestricted = set(data["explicitly_unrestricted"])
    assumptions: list[str] = []
    for field in unresolved:
        if field in CORE_FIELDS:
            assumptions.append(f"核心字段 {field.value} 尚未明确，将按原始问题原文辅助检索")
            data[field.value] = (
                [spec.original_question]
                if field == FieldName.OUTCOMES
                else spec.original_question
            )
        else:
            unrestricted.add(field)
            assumptions.append(f"边界字段 {field.value} 按“不限”处理")
    data["explicitly_unrestricted"] = list(unrestricted)
    return ResearchQuestionSpec.model_validate(data), assumptions


def build_summary(spec: ResearchQuestionSpec, assumptions: list[str]) -> str:
    unrestricted = {field.value for field in spec.explicitly_unrestricted}

    def render(field: str, value: Any) -> str:
        if field in unrestricted:
            return "不限"
        if isinstance(value, list):
            return "、".join(str(item) for item in value) or "未指定"
        return str(value) if value not in (None, "") else "未指定"

    date_range = (
        "不限"
        if {"date_from", "date_to"}.issubset(unrestricted)
        else f"{render('date_from', spec.date_from)} 至 {render('date_to', spec.date_to)}"
    )
    lines = [
        f"研究框架：{spec.framework.value}",
        f"研究目标：{render('research_objective', spec.research_objective)}",
        f"研究对象：{render('population_or_object', spec.population_or_object)}",
        f"核心干预/暴露/概念：{render('intervention_or_exposure', spec.intervention_or_exposure)}",
        f"关注结果：{render('outcomes', spec.outcomes)}",
        f"研究类型：{render('study_types', spec.study_types)}",
        f"时间范围：{date_range}",
        f"语言：{render('languages', spec.languages)}",
        f"全文要求：{spec.fulltext_requirement}",
    ]
    if spec.context:
        lines.append(f"场景：{spec.context}")
    if spec.comparator:
        lines.append(f"对照：{spec.comparator}")
    if assumptions:
        lines.append("系统假设：" + "；".join(assumptions))
    return "\n".join(lines)


def split_values(value: str) -> list[str]:
    return [
        item.strip()
        for item in re.split(r"[,，、;/；]", value)
        if item.strip()
    ]
