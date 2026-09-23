from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Protocol
from urllib.parse import quote

from app.providers.http import JsonHttpClient
from app.schemas.downloads import FullTextSource, ResolvedLocation, VersionType
from app.schemas.retrieval import WorkView


class FullTextResolver(Protocol):
    source: FullTextSource

    async def resolve(self, work: WorkView) -> ResolvedLocation | None: ...


class SourceRateLimiter:
    def __init__(self, requests_per_second: float):
        self.minimum_interval = 1 / max(requests_per_second, 0.1)
        self._lock = asyncio.Lock()
        self._last_request = 0.0

    async def wait(self) -> None:
        async with self._lock:
            delay = self.minimum_interval - (time.monotonic() - self._last_request)
            if delay > 0:
                await asyncio.sleep(delay)
            self._last_request = time.monotonic()


class MetadataPdfResolver:
    source = FullTextSource.METADATA

    async def resolve(self, work: WorkView) -> ResolvedLocation | None:
        if not work.pdf_url:
            return None
        if work.is_oa is not True and not _open_license(work.license):
            return None
        return ResolvedLocation(
            source=self.source,
            url=work.pdf_url,
            license=work.license,
            version_type=VersionType.PUBLISHED,
            landing_url=work.landing_url,
        )


class UnpaywallResolver:
    source = FullTextSource.UNPAYWALL

    def __init__(self, client: JsonHttpClient, email: str | None):
        self.client = client
        self.email = email
        self.limiter = SourceRateLimiter(5)

    async def resolve(self, work: WorkView) -> ResolvedLocation | None:
        if not work.doi or not self.email:
            return None
        await self.limiter.wait()
        response = await self.client.get_json(
            f"https://api.unpaywall.org/v2/{quote(work.doi, safe='/')}",
            params={"email": self.email},
        )
        best = response.payload.get("best_oa_location") or {}
        url = best.get("url_for_pdf")
        if not url:
            return None
        return ResolvedLocation(
            source=self.source,
            url=url,
            license=best.get("license"),
            version_type=_version_type(best.get("version")),
            landing_url=best.get("url") or work.landing_url,
        )


class EuropePmcResolver:
    source = FullTextSource.EUROPE_PMC
    endpoint = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

    def __init__(self, client: JsonHttpClient):
        self.client = client
        self.limiter = SourceRateLimiter(5)

    async def resolve(self, work: WorkView) -> ResolvedLocation | None:
        if not work.pmcid:
            return None
        await self.limiter.wait()
        response = await self.client.get_json(
            self.endpoint,
            params={
                "query": f"PMCID:{work.pmcid}",
                "resultType": "core",
                "format": "json",
                "pageSize": 1,
            },
        )
        results = (response.payload.get("resultList") or {}).get("result") or []
        if not results:
            return None
        urls = (results[0].get("fullTextUrlList") or {}).get("fullTextUrl") or []
        pdf = next(
            (
                item
                for item in urls
                if str(item.get("documentStyle", "")).casefold() == "pdf"
                or ".pdf" in str(item.get("url", "")).casefold()
                or "pdf=render" in str(item.get("url", "")).casefold()
            ),
            None,
        )
        if not pdf or not pdf.get("url"):
            return None
        return ResolvedLocation(
            source=self.source,
            url=pdf["url"],
            license=results[0].get("license") or work.license,
            version_type=VersionType.PUBLISHED,
            landing_url=f"https://europepmc.org/article/MED/{work.pmcid}",
        )


class ArxivResolver:
    source = FullTextSource.ARXIV

    async def resolve(self, work: WorkView) -> ResolvedLocation | None:
        if not work.arxiv_id:
            return None
        identifier = work.arxiv_id.removeprefix("arXiv:").strip()
        if not re.fullmatch(r"[A-Za-z.-]+/\d{7}|\d{4}\.\d{4,5}(?:v\d+)?", identifier):
            return None
        return ResolvedLocation(
            source=self.source,
            url=f"https://arxiv.org/pdf/{identifier}",
            license=work.license,
            version_type=VersionType.PREPRINT,
            landing_url=f"https://arxiv.org/abs/{identifier}",
        )


class CoreResolver:
    source = FullTextSource.CORE
    endpoint = "https://api.core.ac.uk/v3/search/works"

    def __init__(self, client: JsonHttpClient, api_key: str | None):
        self.client = client
        self.api_key = api_key
        self.limiter = SourceRateLimiter(5)

    async def resolve(self, work: WorkView) -> ResolvedLocation | None:
        if not self.api_key or (not work.doi and not work.title):
            return None
        await self.limiter.wait()
        query = f'doi:"{work.doi}"' if work.doi else f'title:"{work.title}"'
        response = await self.client.get_json(
            self.endpoint,
            params={"q": query, "limit": 5},
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        for item in response.payload.get("results") or []:
            url = _core_pdf_url(item)
            if url:
                return ResolvedLocation(
                    source=self.source,
                    url=url,
                    license=item.get("license") or work.license,
                    version_type=_version_type(item.get("version")),
                    landing_url=item.get("sourceFulltextUrls", [None])[0]
                    if item.get("sourceFulltextUrls")
                    else work.landing_url,
                )
        return None


def default_resolvers(
    client: JsonHttpClient,
    *,
    unpaywall_email: str | None,
    core_api_key: str | None,
) -> list[FullTextResolver]:
    return [
        MetadataPdfResolver(),
        UnpaywallResolver(client, unpaywall_email),
        EuropePmcResolver(client),
        ArxivResolver(),
        CoreResolver(client, core_api_key),
    ]


def _open_license(value: str | None) -> bool:
    normalized = str(value or "").casefold()
    return any(
        marker in normalized
        for marker in ("cc-by", "cc by", "creativecommons.org", "public domain", "cc0")
    )


def _version_type(value: Any) -> VersionType:
    normalized = str(value or "").casefold()
    if "publish" in normalized or "versionofrecord" in normalized:
        return VersionType.PUBLISHED
    if "accept" in normalized:
        return VersionType.ACCEPTED
    if "submit" in normalized or "preprint" in normalized:
        return VersionType.PREPRINT
    return VersionType.UNKNOWN


def _core_pdf_url(item: dict[str, Any]) -> str | None:
    direct = item.get("downloadUrl")
    if direct:
        return str(direct)
    for value in item.get("sourceFulltextUrls") or []:
        if ".pdf" in str(value).casefold():
            return str(value)
    for link in item.get("links") or []:
        if isinstance(link, dict) and (
            "pdf" in str(link.get("type", "")).casefold()
            or ".pdf" in str(link.get("url", "")).casefold()
        ):
            return link.get("url")
    return None
