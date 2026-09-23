from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from app.providers.http import JsonResponse
from app.resolvers.fulltext import (
    ArxivResolver,
    CoreResolver,
    EuropePmcResolver,
    MetadataPdfResolver,
    UnpaywallResolver,
)
from app.schemas.retrieval import WorkView


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeJsonClient:
    def __init__(self, payload: dict[str, Any]):
        self.payload = payload
        self.calls: list[tuple[str, dict, dict | None]] = []

    async def get_json(self, url: str, *, params: dict, headers=None) -> JsonResponse:
        self.calls.append((url, params, headers))
        return JsonResponse(payload=self.payload, headers={})


def work(**updates) -> WorkView:
    now = datetime.now(UTC)
    values = {
        "work_id": uuid4(),
        "project_id": uuid4(),
        "title": "Open research article",
        "authors": ["Ada Lovelace"],
        "author_keywords": [],
        "subjects": [],
        "sources": [],
        "matched_query_ids": [],
        "is_retracted": False,
        "relevance_score": 0.8,
        "relevance_reason": "test",
        "user_decision": "unreviewed",
        "tags": [],
        "first_retrieved_at": now,
        "last_updated_at": now,
    }
    values.update(updates)
    return WorkView.model_validate(values)


@pytest.mark.anyio
async def test_metadata_requires_explicit_oa_or_open_license() -> None:
    resolver = MetadataPdfResolver()
    assert (
        await resolver.resolve(
            work(pdf_url="https://example.org/paper.pdf", is_oa=None, license=None)
        )
        is None
    )
    resolved = await resolver.resolve(
        work(
            pdf_url="https://example.org/paper.pdf",
            is_oa=None,
            license="https://creativecommons.org/licenses/by/4.0/",
        )
    )
    assert resolved is not None
    assert resolved.source == "metadata"


@pytest.mark.anyio
async def test_unpaywall_uses_best_oa_pdf_and_email() -> None:
    client = FakeJsonClient(
        {
            "best_oa_location": {
                "url_for_pdf": "https://repository.example/paper.pdf",
                "url": "https://repository.example/item",
                "license": "cc-by",
                "version": "acceptedVersion",
            }
        }
    )
    resolver = UnpaywallResolver(client, "researcher@example.com")
    resolver.limiter.minimum_interval = 0
    resolved = await resolver.resolve(work(doi="10.1234/example"))
    assert resolved is not None
    assert resolved.version_type == "accepted"
    assert client.calls[0][1] == {"email": "researcher@example.com"}
    assert "10.1234/example" in client.calls[0][0]


@pytest.mark.anyio
async def test_europe_pmc_selects_pdf_from_core_result() -> None:
    client = FakeJsonClient(
        {
            "resultList": {
                "result": [
                    {
                        "license": "cc-by",
                        "fullTextUrlList": {
                            "fullTextUrl": [
                                {
                                    "documentStyle": "html",
                                    "url": "https://europepmc.org/articles/PMC1",
                                },
                                {
                                    "documentStyle": "pdf",
                                    "url": "https://europepmc.org/articles/PMC1?pdf=render",
                                },
                            ]
                        },
                    }
                ]
            }
        }
    )
    resolver = EuropePmcResolver(client)
    resolver.limiter.minimum_interval = 0
    resolved = await resolver.resolve(work(pmcid="PMC1"))
    assert resolved is not None
    assert resolved.url.endswith("?pdf=render")
    assert client.calls[0][1]["resultType"] == "core"


@pytest.mark.anyio
async def test_arxiv_only_accepts_valid_identifiers() -> None:
    resolver = ArxivResolver()
    assert await resolver.resolve(work(arxiv_id="../../etc/passwd")) is None
    resolved = await resolver.resolve(work(arxiv_id="2601.12345v2"))
    assert resolved is not None
    assert resolved.url == "https://arxiv.org/pdf/2601.12345v2"
    assert resolved.version_type == "preprint"


@pytest.mark.anyio
async def test_core_requires_key_and_uses_bearer_header() -> None:
    no_key = CoreResolver(FakeJsonClient({}), None)
    assert await no_key.resolve(work(doi="10.1234/example")) is None

    client = FakeJsonClient(
        {
            "results": [
                {
                    "downloadUrl": "https://core.ac.uk/download/123.pdf",
                    "license": "cc-by",
                }
            ]
        }
    )
    resolver = CoreResolver(client, "core-secret")
    resolver.limiter.minimum_interval = 0
    resolved = await resolver.resolve(work(doi="10.1234/example"))
    assert resolved is not None
    assert resolved.source == "core"
    assert client.calls[0][2] == {"Authorization": "Bearer core-secret"}
