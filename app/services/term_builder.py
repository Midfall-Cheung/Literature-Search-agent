from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from typing import Any, Protocol

from app.schemas.question import ResearchQuestionSpec
from app.schemas.terms import (
    FieldHint,
    GeneratedTermCandidates,
    QueryPreview,
    QueryPurpose,
    QueryVariant,
    TermCandidate,
    TermLanguage,
    TermRecord,
    TermSource,
    TermTable,
    TermType,
)


GLOSSARY: dict[str, list[tuple[str, TermLanguage, TermType]]] = {
    "人工智能": [
        ("artificial intelligence", TermLanguage.EN, TermType.TRANSLATION),
        ("AI", TermLanguage.EN, TermType.ABBREVIATION),
    ],
    "artificial intelligence": [
        ("人工智能", TermLanguage.ZH, TermType.TRANSLATION),
        ("AI", TermLanguage.EN, TermType.ABBREVIATION),
    ],
    "生成式人工智能": [
        ("generative artificial intelligence", TermLanguage.EN, TermType.TRANSLATION),
        ("generative AI", TermLanguage.EN, TermType.SYNONYM),
    ],
    "generative artificial intelligence": [
        ("生成式人工智能", TermLanguage.ZH, TermType.TRANSLATION),
        ("generative AI", TermLanguage.EN, TermType.SYNONYM),
    ],
    "大语言模型": [
        ("large language model", TermLanguage.EN, TermType.TRANSLATION),
        ("LLM", TermLanguage.EN, TermType.ABBREVIATION),
    ],
    "large language model": [
        ("大语言模型", TermLanguage.ZH, TermType.TRANSLATION),
        ("LLM", TermLanguage.EN, TermType.ABBREVIATION),
    ],
    "文献检索": [
        ("literature search", TermLanguage.EN, TermType.TRANSLATION),
        ("information retrieval", TermLanguage.EN, TermType.SYNONYM),
    ],
    "literature search": [
        ("文献检索", TermLanguage.ZH, TermType.TRANSLATION),
        ("information retrieval", TermLanguage.EN, TermType.SYNONYM),
    ],
    "系统综述": [
        ("systematic review", TermLanguage.EN, TermType.TRANSLATION),
    ],
    "systematic review": [
        ("系统综述", TermLanguage.ZH, TermType.TRANSLATION),
    ],
    "高校教师": [
        ("university faculty", TermLanguage.EN, TermType.TRANSLATION),
        ("higher education teachers", TermLanguage.EN, TermType.SYNONYM),
    ],
    "大学生": [
        ("university students", TermLanguage.EN, TermType.TRANSLATION),
        ("college students", TermLanguage.EN, TermType.SYNONYM),
    ],
    "检索效率": [("search efficiency", TermLanguage.EN, TermType.TRANSLATION)],
}


def normalize_term(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    normalized = normalized.removeprefix('"').removesuffix('"')
    return re.sub(r"\s+", " ", normalized).casefold()


def detect_language(value: str) -> TermLanguage:
    has_cjk = bool(re.search(r"[\u3400-\u9fff]", value))
    has_latin = bool(re.search(r"[A-Za-z]", value))
    if has_cjk and not has_latin:
        return TermLanguage.ZH
    if has_latin and not has_cjk:
        return TermLanguage.EN
    return TermLanguage.OTHER


class TermExpander(Protocol):
    def expand(
        self, spec: ResearchQuestionSpec, seed_terms: Sequence[TermCandidate]
    ) -> list[TermCandidate]: ...


class HeuristicTermExpander:
    """Small, transparent offline expansion set; every derived term is labelled."""

    def expand(
        self, spec: ResearchQuestionSpec, seed_terms: Sequence[TermCandidate]
    ) -> list[TermCandidate]:
        del spec
        expanded: list[TermCandidate] = []
        for seed in seed_terms:
            if seed.term_type == TermType.EXCLUSION:
                continue
            for term, language, term_type in GLOSSARY.get(normalize_term(seed.term), []):
                expanded.append(
                    TermCandidate(
                        concept_id=seed.concept_id,
                        concept_name=seed.concept_name,
                        term=term,
                        language=language,
                        term_type=term_type,
                        source=TermSource.HEURISTIC,
                        field_hint=seed.field_hint,
                        notes=f"由内置可审计词表从“{seed.term}”扩展",
                    )
                )
            expanded.extend(self._parenthesized_variants(seed))
        return expanded

    @staticmethod
    def _parenthesized_variants(seed: TermCandidate) -> list[TermCandidate]:
        match = re.match(r"^(.+?)\s*[（(]([^()（）]+)[）)]$", seed.term)
        if not match:
            return []
        return [
            TermCandidate(
                concept_id=seed.concept_id,
                concept_name=seed.concept_name,
                term=value.strip(),
                language=detect_language(value),
                term_type=(
                    TermType.ABBREVIATION
                    if re.fullmatch(r"[A-Z][A-Z0-9-]{1,15}", value.strip())
                    else TermType.SYNONYM
                ),
                source=TermSource.HEURISTIC,
                field_hint=seed.field_hint,
                notes=f"从括号表达“{seed.term}”拆分",
            )
            for value in match.groups()
        ]


class StructuredLLMTermExpander:
    """Provider-neutral structured-output adapter with enforced LLM provenance."""

    def __init__(self, chat_model: Any):
        self.structured_model = chat_model.with_structured_output(
            GeneratedTermCandidates
        )

    def expand(
        self, spec: ResearchQuestionSpec, seed_terms: Sequence[TermCandidate]
    ) -> list[TermCandidate]:
        concepts = {
            item.concept_id: item.concept_name
            for item in seed_terms
            if item.term_type != TermType.EXCLUSION
        }
        prompt = f"""你是学术检索词扩展器。为每个概念生成少量高价值的中英文同义词、缩写、拼写变体或专业翻译。
只使用给定 concept_id；不要生成数据库查询，不要做概念间笛卡尔积。
不能把模型生成词标记为 controlled_vocab。歧义词应在 notes 中说明。

结构化研究问题：{spec.model_dump_json()}
概念：{concepts}
已有词：{[item.term for item in seed_terms]}
"""
        result = self.structured_model.invoke(prompt)
        parsed = (
            result
            if isinstance(result, GeneratedTermCandidates)
            else GeneratedTermCandidates.model_validate(result)
        )
        candidates: list[TermCandidate] = []
        for item in parsed.terms:
            if item.concept_id not in concepts:
                continue
            candidates.append(
                TermCandidate(
                    concept_id=item.concept_id,
                    concept_name=concepts[item.concept_id],
                    term=item.term,
                    language=item.language,
                    term_type=item.term_type,
                    source=TermSource.LLM,
                    field_hint=item.field_hint,
                    notes=item.notes,
                )
            )
        return candidates


class CompositeTermExpander:
    def __init__(self, expanders: Sequence[TermExpander]):
        self.expanders = expanders

    def expand(
        self, spec: ResearchQuestionSpec, seed_terms: Sequence[TermCandidate]
    ) -> list[TermCandidate]:
        return [
            candidate
            for expander in self.expanders
            for candidate in expander.expand(spec, seed_terms)
        ]


class TermBuilder:
    def __init__(self, expander: TermExpander | None = None):
        self.expander = expander or HeuristicTermExpander()

    def build(self, spec: ResearchQuestionSpec) -> list[TermCandidate]:
        if not spec.confirmed:
            raise ValueError("必须先确认结构化研究问题")
        seeds = build_seed_terms(spec)
        return deduplicate_candidates([*seeds, *self.expander.expand(spec, seeds)])


def build_seed_terms(spec: ResearchQuestionSpec) -> list[TermCandidate]:
    candidates: list[TermCandidate] = []

    def add(
        concept_id: str,
        concept_name: str,
        values: str | Iterable[str] | None,
        *,
        enabled: bool = True,
        term_type: TermType = TermType.PREFERRED,
    ) -> None:
        if values is None:
            return
        items = [values] if isinstance(values, str) else list(values)
        for value in items:
            value = value.strip()
            if not value:
                continue
            candidates.append(
                TermCandidate(
                    concept_id=concept_id,
                    concept_name=concept_name,
                    term=value,
                    language=detect_language(value),
                    term_type=term_type,
                    source=TermSource.USER,
                    enabled=enabled,
                    notes="来自用户确认的结构化研究问题",
                )
            )

    add("C1", "研究对象", spec.population_or_object)
    add("C2", "核心干预/暴露/概念", spec.intervention_or_exposure)
    add("C3", "关注结果", spec.outcomes)
    add("C4", "研究场景", spec.context)
    add("C5", "对照", spec.comparator, enabled=False)
    add("M1", "必须包含", spec.must_include)
    add("X1", "排除词", spec.exclude, term_type=TermType.EXCLUSION)
    return candidates


def deduplicate_candidates(candidates: Sequence[TermCandidate]) -> list[TermCandidate]:
    deduplicated: dict[tuple[str, str], TermCandidate] = {}
    for candidate in candidates:
        key = (candidate.concept_id, normalize_term(candidate.term))
        if key not in deduplicated:
            deduplicated[key] = candidate
    return sorted(
        deduplicated.values(),
        key=lambda item: (
            concept_sort_key(item.concept_id),
            item.term_type != TermType.PREFERRED,
            normalize_term(item.term),
        ),
    )


def concept_sort_key(concept_id: str) -> tuple[str, int, str]:
    match = re.match(r"([A-Za-z]+)(\d+)$", concept_id)
    if not match:
        return (concept_id, 0, concept_id)
    prefix_priority = {"C": "0", "M": "1", "X": "2"}.get(
        match.group(1).upper(), "9"
    )
    return (prefix_priority, int(match.group(2)), concept_id)


def build_query_preview(term_table: TermTable) -> QueryPreview:
    if term_table.status.value == "not_generated":
        raise ValueError("词表尚未生成")
    positive = _group_terms(
        item
        for item in term_table.terms
        if item.enabled and item.term_type != TermType.EXCLUSION
    )
    exclusions = [
        item
        for item in term_table.terms
        if item.enabled and item.term_type == TermType.EXCLUSION
    ]
    if len(positive) < 2:
        raise ValueError("至少需要两个已启用的非排除概念块")

    concept_ids = list(positive)
    broad_ids = concept_ids[:2]
    variants = [
        QueryVariant(
            purpose=QueryPurpose.BROAD,
            canonical_query=_compile_blocks(positive, broad_ids, []),
            included_concept_ids=broad_ids,
            notes="前两个核心概念块，高召回预览",
        ),
        QueryVariant(
            purpose=QueryPurpose.FOCUSED,
            canonical_query=_compile_blocks(positive, concept_ids, exclusions),
            included_concept_ids=concept_ids,
            notes="全部启用概念块；排除词仅在此预览中谨慎应用",
        ),
    ]

    preferred = {
        concept_id: [item for item in items if item.term_type == TermType.PREFERRED]
        for concept_id, items in positive.items()
    }
    preferred = {key: value for key, value in preferred.items() if value}
    if len(preferred) >= 2:
        variants.append(
            QueryVariant(
                purpose=QueryPurpose.EXACT_PHRASE,
                canonical_query=_compile_blocks(preferred, list(preferred), []),
                included_concept_ids=list(preferred),
                notes="仅使用用户确认的首选词短语",
            )
        )

    controlled = {
        concept_id: [
            item for item in items if item.term_type == TermType.CONTROLLED_VOCAB
        ]
        for concept_id, items in positive.items()
    }
    controlled = {key: value for key, value in controlled.items() if value}
    if controlled:
        variants.append(
            QueryVariant(
                purpose=QueryPurpose.CONTROLLED_VOCAB,
                canonical_query=_compile_blocks(controlled, list(controlled), []),
                included_concept_ids=list(controlled),
                notes="仅展示用户或受控词表适配器提供的主题词",
            )
        )
    return QueryPreview(
        project_id=term_table.project_id,
        term_set_version=term_table.version,
        variants=variants,
    )


def _group_terms(terms: Iterable[TermRecord]) -> dict[str, list[TermRecord]]:
    result: dict[str, list[TermRecord]] = {}
    for term in sorted(terms, key=lambda item: concept_sort_key(item.concept_id)):
        result.setdefault(term.concept_id, []).append(term)
    return result


def _compile_blocks(
    grouped: dict[str, list[TermRecord]],
    concept_ids: list[str],
    exclusions: Sequence[TermRecord],
) -> str:
    blocks: list[str] = []
    for concept_id in concept_ids:
        rendered = " OR ".join(_quote(item.term) for item in grouped[concept_id])
        blocks.append(f"({rendered})")
    query = " AND ".join(blocks)
    if exclusions:
        query += " NOT (" + " OR ".join(_quote(item.term) for item in exclusions) + ")"
    return query


def _quote(term: str) -> str:
    escaped = term.replace('"', '\\"')
    return escaped if re.fullmatch(r"[A-Za-z0-9_-]+", escaped) else f'"{escaped}"'

