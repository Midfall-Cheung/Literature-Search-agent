from __future__ import annotations

import html
import hashlib
import json
import re
from datetime import date
from typing import Any, Protocol

from app.providers.http import JsonHttpClient
from app.schemas.query_plans import ProviderName, ProviderQuery
from app.schemas.retrieval import NormalizedWorkCandidate, SearchPage


class RetrievalProvider(Protocol):
    name: ProviderName

    async def search(
        self,
        query: ProviderQuery,
        *,
        cursor: str | None,
        limit: int,
    ) -> SearchPage: ...

    def normalize(self, raw_item: dict[str, Any]) -> NormalizedWorkCandidate: ...


class BaseRetrievalProvider:
    name: ProviderName

    def __init__(
        self,
        client: JsonHttpClient,
        *,
        api_key: str | None = None,
        mailto: str | None = None,
    ):
        self.client = client
        self.api_key = api_key
        self.mailto = mailto


class OpenAlexRetriever(BaseRetrievalProvider):
    name = ProviderName.OPENALEX

    async def search(
        self,
        query: ProviderQuery,
        *,
        cursor: str | None,
        limit: int,
    ) -> SearchPage:
        params = dict(query.request_params)
        params["cursor"] = cursor or "*"
        params.pop("per-page", None)
        params["per_page"] = limit
        if self.api_key:
            params["api_key"] = self.api_key
        response = await self.client.get_json(query.endpoint, params=params)
        meta = response.payload.get("meta") or {}
        return SearchPage(
            items=response.payload.get("results") or [],
            next_cursor=meta.get("next_cursor"),
            estimated_total=meta.get("count"),
        )

    def normalize(self, raw_item: dict[str, Any]) -> NormalizedWorkCandidate:
        ids = raw_item.get("ids") or {}
        location = raw_item.get("best_oa_location") or {}
        primary_location = raw_item.get("primary_location") or {}
        source = primary_location.get("source") or {}
        biblio = raw_item.get("biblio") or {}
        open_access = raw_item.get("open_access") or {}
        record_id = _last_path(raw_item.get("id")) or _doi(
            ids.get("doi") or raw_item.get("doi")
        )
        return NormalizedWorkCandidate(
            provider=self.name,
            provider_record_id=record_id or _fallback_record_id(raw_item),
            title=_first_text(raw_item.get("title"), "Untitled"),
            authors=[
                item.get("author", {}).get("display_name")
                for item in raw_item.get("authorships") or []
                if item.get("author", {}).get("display_name")
            ],
            year=_integer(raw_item.get("publication_year")),
            publication_date=_date(raw_item.get("publication_date")),
            venue=source.get("display_name"),
            volume=biblio.get("volume"),
            issue=biblio.get("issue"),
            pages=_pages(biblio.get("first_page"), biblio.get("last_page")),
            work_type=raw_item.get("type"),
            language=raw_item.get("language"),
            abstract=_openalex_abstract(raw_item.get("abstract_inverted_index")),
            author_keywords=[
                item.get("display_name")
                for item in raw_item.get("keywords") or []
                if item.get("display_name")
            ],
            subjects=[
                item.get("display_name")
                for item in raw_item.get("topics") or []
                if item.get("display_name")
            ],
            doi=_doi(ids.get("doi") or raw_item.get("doi")),
            pmid=_last_path(ids.get("pmid")),
            openalex_id=_last_path(raw_item.get("id")),
            citation_count=_integer(raw_item.get("cited_by_count")),
            is_retracted=bool(raw_item.get("is_retracted", False)),
            retraction_source="openalex" if raw_item.get("is_retracted") else None,
            is_oa=open_access.get("is_oa"),
            oa_status=open_access.get("oa_status"),
            license=location.get("license"),
            landing_url=location.get("landing_page_url") or raw_item.get("id"),
            pdf_url=location.get("pdf_url"),
        )


class CrossrefRetriever(BaseRetrievalProvider):
    name = ProviderName.CROSSREF

    async def search(
        self,
        query: ProviderQuery,
        *,
        cursor: str | None,
        limit: int,
    ) -> SearchPage:
        params = dict(query.request_params)
        params["cursor"] = cursor or "*"
        params["rows"] = limit
        if self.mailto:
            params["mailto"] = self.mailto
        response = await self.client.get_json(query.endpoint, params=params)
        message = response.payload.get("message") or {}
        return SearchPage(
            items=message.get("items") or [],
            next_cursor=message.get("next-cursor"),
            estimated_total=message.get("total-results"),
        )

    def normalize(self, raw_item: dict[str, Any]) -> NormalizedWorkCandidate:
        publication_date = _crossref_date(raw_item)
        links = raw_item.get("link") or []
        pdf_link = next(
            (
                item.get("URL")
                for item in links
                if "pdf" in str(item.get("content-type", "")).casefold()
            ),
            None,
        )
        licenses = raw_item.get("license") or []
        record_id = _first_text(raw_item.get("DOI") or raw_item.get("URL"))
        return NormalizedWorkCandidate(
            provider=self.name,
            provider_record_id=record_id or _fallback_record_id(raw_item),
            title=_first_text(raw_item.get("title"), "Untitled"),
            authors=[
                " ".join(
                    part
                    for part in (item.get("given"), item.get("family"))
                    if part
                )
                for item in raw_item.get("author") or []
            ],
            year=publication_date.year if publication_date else None,
            publication_date=publication_date,
            venue=_first_text(raw_item.get("container-title")),
            volume=raw_item.get("volume"),
            issue=raw_item.get("issue"),
            pages=raw_item.get("page"),
            work_type=raw_item.get("type"),
            language=raw_item.get("language"),
            abstract=_strip_html(raw_item.get("abstract")),
            subjects=raw_item.get("subject") or [],
            doi=_doi(raw_item.get("DOI")),
            citation_count=_integer(raw_item.get("is-referenced-by-count")),
            license=licenses[0].get("URL") if licenses else None,
            landing_url=raw_item.get("URL"),
            pdf_url=pdf_link,
        )


class SemanticScholarRetriever(BaseRetrievalProvider):
    name = ProviderName.SEMANTIC_SCHOLAR

    async def search(
        self,
        query: ProviderQuery,
        *,
        cursor: str | None,
        limit: int,
    ) -> SearchPage:
        params = dict(query.request_params)
        if cursor:
            params["token"] = cursor
        else:
            params.pop("token", None)
        params["limit"] = limit
        headers = {"x-api-key": self.api_key} if self.api_key else None
        response = await self.client.get_json(
            query.endpoint, params=params, headers=headers
        )
        return SearchPage(
            items=response.payload.get("data") or [],
            next_cursor=response.payload.get("token"),
            estimated_total=response.payload.get("total"),
        )

    def normalize(self, raw_item: dict[str, Any]) -> NormalizedWorkCandidate:
        ids = raw_item.get("externalIds") or {}
        oa = raw_item.get("openAccessPdf") or {}
        publication_types = raw_item.get("publicationTypes") or []
        record_id = _first_text(raw_item.get("paperId") or ids.get("DOI"))
        return NormalizedWorkCandidate(
            provider=self.name,
            provider_record_id=record_id or _fallback_record_id(raw_item),
            title=_first_text(raw_item.get("title"), "Untitled"),
            authors=[
                item.get("name")
                for item in raw_item.get("authors") or []
                if item.get("name")
            ],
            year=_integer(raw_item.get("year")),
            publication_date=_date(raw_item.get("publicationDate")),
            venue=raw_item.get("venue"),
            work_type=publication_types[0] if publication_types else None,
            abstract=raw_item.get("abstract"),
            subjects=[
                item.get("category") or item.get("source")
                for item in raw_item.get("s2FieldsOfStudy") or []
                if item.get("category") or item.get("source")
            ],
            doi=_doi(ids.get("DOI")),
            pmid=_first_text(ids.get("PubMed") or ids.get("PMID")),
            arxiv_id=_first_text(ids.get("ArXiv")),
            s2_id=_first_text(raw_item.get("paperId")),
            citation_count=_integer(raw_item.get("citationCount")),
            is_oa=raw_item.get("isOpenAccess"),
            oa_status=oa.get("status"),
            license=oa.get("license"),
            landing_url=raw_item.get("url"),
            pdf_url=oa.get("url"),
        )


def default_retrievers(
    client: JsonHttpClient,
    *,
    openalex_api_key: str | None = None,
    semantic_scholar_api_key: str | None = None,
    crossref_mailto: str | None = None,
) -> dict[ProviderName, RetrievalProvider]:
    providers: list[RetrievalProvider] = [
        OpenAlexRetriever(client, api_key=openalex_api_key),
        CrossrefRetriever(client, mailto=crossref_mailto),
        SemanticScholarRetriever(client, api_key=semantic_scholar_api_key),
    ]
    return {provider.name: provider for provider in providers}


def _first_text(value: Any, default: str | None = None) -> str | None:
    if isinstance(value, list):
        value = value[0] if value else None
    if value is None:
        return default
    rendered = str(value).strip()
    return rendered or default


def _integer(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def _crossref_date(item: dict[str, Any]) -> date | None:
    for key in ("published-print", "published-online", "published", "issued"):
        parts = (item.get(key) or {}).get("date-parts") or []
        if not parts:
            continue
        values = parts[0]
        try:
            return date(
                int(values[0]),
                int(values[1]) if len(values) > 1 else 1,
                int(values[2]) if len(values) > 2 else 1,
            )
        except (TypeError, ValueError):
            continue
    return None


def _doi(value: Any) -> str | None:
    rendered = _first_text(value)
    if not rendered:
        return None
    rendered = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", rendered, flags=re.I)
    return rendered.casefold().strip()


def _last_path(value: Any) -> str | None:
    rendered = _first_text(value)
    return rendered.rstrip("/").rsplit("/", 1)[-1] if rendered else None


def _pages(first: Any, last: Any) -> str | None:
    if first and last and str(first) != str(last):
        return f"{first}-{last}"
    return _first_text(first or last)


def _strip_html(value: Any) -> str | None:
    rendered = _first_text(value)
    if not rendered:
        return None
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(rendered))).strip()


def _openalex_abstract(index: Any) -> str | None:
    if not isinstance(index, dict) or not index:
        return None
    positioned = [
        (position, word)
        for word, positions in index.items()
        for position in positions
    ]
    return " ".join(word for _, word in sorted(positioned))


def _fallback_record_id(item: dict[str, Any]) -> str:
    encoded = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
    return "hash:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()
