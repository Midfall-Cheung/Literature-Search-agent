from __future__ import annotations

import re
from typing import Protocol

from app.schemas.query_plans import ProviderName, ProviderQueryDraft
from app.schemas.question import ResearchQuestionSpec
from app.schemas.terms import QueryPurpose, TermTable
from app.services.term_builder import build_query_preview


class SearchProvider(Protocol):
    name: ProviderName

    async def compile_query(
        self,
        spec: ResearchQuestionSpec,
        term_table: TermTable,
        *,
        page_size: int,
        max_records: int,
    ) -> list[ProviderQueryDraft]: ...


def _date_filters(spec: ResearchQuestionSpec) -> dict[str, int]:
    result: dict[str, int] = {}
    if spec.date_from is not None:
        result["from_year"] = spec.date_from
    if spec.date_to is not None:
        result["to_year"] = spec.date_to
    return result


def _audit_filters(spec: ResearchQuestionSpec) -> dict[str, object]:
    return {
        **_date_filters(spec),
        "languages": spec.languages,
        "study_types": spec.study_types,
        "fulltext_requirement": spec.fulltext_requirement,
    }


class OpenAlexProvider:
    name = ProviderName.OPENALEX
    endpoint = "https://api.openalex.org/works"

    async def compile_query(
        self,
        spec: ResearchQuestionSpec,
        term_table: TermTable,
        *,
        page_size: int,
        max_records: int,
    ) -> list[ProviderQueryDraft]:
        variants = build_query_preview(term_table).variants
        filters: list[str] = []
        if spec.date_from is not None:
            filters.append(f"from_publication_date:{spec.date_from}-01-01")
        if spec.date_to is not None:
            filters.append(f"to_publication_date:{spec.date_to}-12-31")
        if spec.languages:
            filters.append("language:" + "|".join(spec.languages))
        notes = []
        if spec.study_types:
            notes.append(
                "研究类型保留为审计条件；未自动映射为 OpenAlex work type，"
                "以避免错误限缩。"
            )
        return [
            ProviderQueryDraft(
                provider=self.name,
                purpose=item.purpose,
                canonical_query=item.canonical_query,
                provider_query=item.canonical_query,
                endpoint=self.endpoint,
                request_params={
                    "search": item.canonical_query,
                    **({"filter": ",".join(filters)} if filters else {}),
                    "per-page": page_size,
                    "cursor": "*",
                },
                filters=_audit_filters(spec),
                page_size=page_size,
                max_records=max_records,
                notes=["使用 OpenAlex search 布尔语法。", *notes],
            )
            for item in variants
        ]


class CrossrefProvider:
    name = ProviderName.CROSSREF
    endpoint = "https://api.crossref.org/works"

    async def compile_query(
        self,
        spec: ResearchQuestionSpec,
        term_table: TermTable,
        *,
        page_size: int,
        max_records: int,
    ) -> list[ProviderQueryDraft]:
        variants = build_query_preview(term_table).variants
        date_filters: list[str] = []
        if spec.date_from is not None:
            date_filters.append(f"from-pub-date:{spec.date_from}-01-01")
        if spec.date_to is not None:
            date_filters.append(f"until-pub-date:{spec.date_to}-12-31")
        return [
            ProviderQueryDraft(
                provider=self.name,
                purpose=item.purpose,
                canonical_query=item.canonical_query,
                provider_query=_crossref_bibliographic_query(item.canonical_query),
                endpoint=self.endpoint,
                request_params={
                    "query.bibliographic": _crossref_bibliographic_query(
                        item.canonical_query
                    ),
                    **({"filter": ",".join(date_filters)} if date_filters else {}),
                    "rows": page_size,
                    "cursor": "*",
                },
                filters=_audit_filters(spec),
                page_size=page_size,
                max_records=max_records,
                notes=[
                    "Crossref query.bibliographic 不保证布尔逻辑，"
                    "已降级为关键词相关性查询。",
                    "语言和研究类型仅保留为审计条件，"
                    "需在结果规范化后筛选。",
                ],
            )
            for item in variants
        ]


class SemanticScholarProvider:
    name = ProviderName.SEMANTIC_SCHOLAR
    endpoint = "https://api.semanticscholar.org/graph/v1/paper/search/bulk"

    async def compile_query(
        self,
        spec: ResearchQuestionSpec,
        term_table: TermTable,
        *,
        page_size: int,
        max_records: int,
    ) -> list[ProviderQueryDraft]:
        variants = build_query_preview(term_table).variants
        publication_types = _semantic_scholar_publication_types(spec.study_types)
        date_range = _semantic_scholar_date_range(spec)
        return [
            ProviderQueryDraft(
                provider=self.name,
                purpose=item.purpose,
                canonical_query=item.canonical_query,
                provider_query=_semantic_scholar_query(item.canonical_query),
                endpoint=self.endpoint,
                request_params={
                    "query": _semantic_scholar_query(item.canonical_query),
                    **(
                        {"publicationDateOrYear": date_range}
                        if date_range
                        else {}
                    ),
                    **(
                        {"publicationTypes": ",".join(publication_types)}
                        if publication_types
                        else {}
                    ),
                    "fields": (
                        "title,abstract,authors,year,publicationDate,venue,externalIds,"
                        "citationCount,isOpenAccess,openAccessPdf,publicationTypes"
                    ),
                    "limit": page_size,
                },
                filters=_audit_filters(spec),
                page_size=page_size,
                max_records=max_records,
                notes=[
                    "使用 Semantic Scholar bulk search 的 +、|、- 布尔语法。",
                    "语言条件仅保留为审计条件，需在结果规范化后筛选。",
                ],
            )
            for item in variants
        ]


def _crossref_bibliographic_query(query: str) -> str:
    phrases = re.findall(r'"((?:\\.|[^"\\])*)"', query)
    without_phrases = re.sub(r'"(?:\\.|[^"\\])*"', " ", query)
    bare = [
        token
        for token in re.findall(r"[\w-]+", without_phrases, flags=re.UNICODE)
        if token.upper() not in {"AND", "OR", "NOT"}
    ]
    return " ".join([*(item.replace('\\"', '"') for item in phrases), *bare])


def _semantic_scholar_query(query: str) -> str:
    translated = query.replace(" AND ", " + ").replace(" OR ", " | ")
    translated = translated.replace(" NOT (", " -(").replace(" NOT ", " -")
    return re.sub(r"\s+", " ", translated).strip()


def _semantic_scholar_date_range(spec: ResearchQuestionSpec) -> str | None:
    if spec.date_from is not None and spec.date_to is not None:
        return f"{spec.date_from}:{spec.date_to}"
    if spec.date_from is not None:
        return f"{spec.date_from}:"
    if spec.date_to is not None:
        return f":{spec.date_to}"
    return None


def _semantic_scholar_publication_types(study_types: list[str]) -> list[str]:
    mapping = {
        "systematic_review": "Review",
        "meta_analysis": "MetaAnalysis",
        "randomized_controlled_trial": "ClinicalTrial",
    }
    return list(dict.fromkeys(mapping[item] for item in study_types if item in mapping))


def default_providers() -> dict[ProviderName, SearchProvider]:
    providers: list[SearchProvider] = [
        OpenAlexProvider(),
        CrossrefProvider(),
        SemanticScholarProvider(),
    ]
    return {provider.name: provider for provider in providers}
