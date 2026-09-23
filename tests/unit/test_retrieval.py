import csv
import io
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.providers.http import JsonResponse


class FakeProviderClient:
    def __init__(self, *, fail_crossref: bool = False):
        self.fail_crossref = fail_crossref
        self.calls: list[tuple[str, dict[str, Any], dict[str, str] | None]] = []

    async def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> JsonResponse:
        self.calls.append((url, params, headers))
        if "crossref" in url:
            if self.fail_crossref:
                raise RuntimeError("temporary crossref failure")
            return JsonResponse(
                payload={
                    "message": {
                        "total-results": 1,
                        "items": [
                            {
                                "DOI": "10.1234/Shared",
                                "title": ["LLM tools for literature search"],
                                "author": [{"given": "Ada", "family": "Lovelace"}],
                                "published-online": {"date-parts": [[2024, 2, 3]]},
                                "container-title": ["Metadata Journal"],
                                "type": "journal-article",
                                "is-referenced-by-count": 7,
                                "URL": "https://doi.org/10.1234/shared",
                            }
                        ],
                    }
                },
                headers={},
            )
        if "semanticscholar" in url:
            return JsonResponse(
                payload={
                    "total": 1,
                    "data": [
                        {
                            "paperId": "s2-1",
                            "externalIds": {"DOI": "10.1234/shared"},
                            "title": "LLM tools for literature search",
                            "authors": [{"name": "Ada Lovelace"}],
                            "year": 2024,
                            "publicationDate": "2024-02-03",
                            "venue": "Metadata Journal",
                            "abstract": "large language model literature search efficiency",
                            "citationCount": 8,
                            "isOpenAccess": True,
                            "openAccessPdf": {
                                "url": "https://example.org/paper.pdf",
                                "status": "GREEN",
                                "license": "CC-BY",
                            },
                        }
                    ],
                },
                headers={},
            )
        return JsonResponse(
            payload={
                "meta": {"count": 1, "next_cursor": None},
                "results": [
                    {
                        "id": "https://openalex.org/W1",
                        "doi": "https://doi.org/10.1234/SHARED",
                        "title": "LLM tools for literature search",
                        "publication_year": 2024,
                        "publication_date": "2024-02-03",
                        "authorships": [
                            {"author": {"display_name": "Ada Lovelace"}}
                        ],
                        "primary_location": {
                            "source": {"display_name": "Metadata Journal"}
                        },
                        "abstract_inverted_index": {
                            "large": [0],
                            "language": [1],
                            "model": [2],
                            "literature": [3],
                            "search": [4],
                        },
                        "cited_by_count": 6,
                        "open_access": {"is_oa": True, "oa_status": "green"},
                    }
                ],
            },
            headers={},
        )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def make_client(tmp_path: Path, provider_client: FakeProviderClient) -> AsyncClient:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'business.db'}",
        checkpoint_database_path=tmp_path / "checkpoints.db",
        crossref_mailto="test@example.com",
        openalex_api_key="openalex-test-key",
        semantic_scholar_api_key="s2-test-key",
    )
    return AsyncClient(
        transport=ASGITransport(
            app=create_app(settings, retrieval_http_client=provider_client)
        ),
        base_url="http://test",
    )


async def prepare_confirmed_plan(client: AsyncClient) -> str:
    created = await client.post(
        "/projects",
        json={
            "original_question": (
                "对象：高校教师；核心概念：大语言模型；结果：文献检索效率；"
                "系统综述，2020-2026，中英文"
            )
        },
    )
    project_id = created.json()["project_id"]
    await client.post(
        f"/projects/{project_id}/confirm-question", json={"accepted": True}
    )
    terms = await client.post(
        f"/projects/{project_id}/terms/generate",
        json={"replace_existing": False},
    )
    await client.post(
        f"/projects/{project_id}/confirm-terms",
        json={"expected_version": terms.json()["version"]},
    )
    plan = await client.post(
        f"/projects/{project_id}/query-plans/compile",
        json={"max_records": 20},
    )
    await client.post(
        f"/projects/{project_id}/confirm-query-plan",
        json={"expected_version": plan.json()["version"]},
    )
    return project_id


@pytest.mark.anyio
async def test_search_normalizes_deduplicates_traces_and_exports(tmp_path: Path) -> None:
    provider_client = FakeProviderClient()
    async with make_client(tmp_path, provider_client) as client:
        project_id = await prepare_confirmed_plan(client)
        response = await client.post(
            f"/projects/{project_id}/search-runs",
            json={"purposes": ["broad"], "max_records_per_query": 10},
        )
        assert response.status_code == 201, response.text
        run = response.json()
        assert run["status"] == "completed"
        assert run["raw_record_count"] == 3
        assert run["work_count"] == 1
        assert len(run["query_executions"]) == 3
        assert all(item["max_records"] == 10 for item in run["query_executions"])

        works = await client.get(f"/projects/{project_id}/works")
        assert works.status_code == 200
        assert works.json()["total"] == 1
        work = works.json()["items"][0]
        assert work["doi"] == "10.1234/shared"
        assert set(work["sources"]) == {
            "openalex",
            "crossref",
            "semantic_scholar",
        }
        assert len(work["matched_query_ids"]) == 3
        assert work["citation_count"] == 8
        assert work["citation_source"] == "semantic_scholar"
        assert work["is_oa"] is True
        assert work["pdf_url"] == "https://example.org/paper.pdf"
        assert work["relevance_score"] > 0

        exported = await client.get(f"/projects/{project_id}/exports/works.csv")
        assert exported.status_code == 200
        assert exported.content.startswith(b"\xef\xbb\xbf")
        rows = list(
            csv.DictReader(io.StringIO(exported.content.decode("utf-8-sig")))
        )
        assert len(rows) == 1
        assert rows[0]["doi"] == "10.1234/shared"

        openalex_call = next(call for call in provider_client.calls if "openalex" in call[0])
        assert openalex_call[1]["api_key"] == "openalex-test-key"
        crossref_call = next(call for call in provider_client.calls if "crossref" in call[0])
        assert crossref_call[1]["mailto"] == "test@example.com"
        semantic_call = next(
            call for call in provider_client.calls if "semanticscholar" in call[0]
        )
        assert semantic_call[2] == {"x-api-key": "s2-test-key"}


@pytest.mark.anyio
async def test_partial_run_can_resume_failed_provider(tmp_path: Path) -> None:
    provider_client = FakeProviderClient(fail_crossref=True)
    async with make_client(tmp_path, provider_client) as client:
        project_id = await prepare_confirmed_plan(client)
        started = await client.post(
            f"/projects/{project_id}/search-runs",
            json={"purposes": ["broad"], "max_records_per_query": 5},
        )
        assert started.status_code == 201
        assert started.json()["status"] == "partial"
        assert started.json()["error_count"] == 1
        run_id = started.json()["run_id"]

        provider_client.fail_crossref = False
        resumed = await client.post(
            f"/projects/{project_id}/search-runs/{run_id}/resume"
        )
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["status"] == "completed"
        assert resumed.json()["raw_record_count"] == 3
        assert resumed.json()["work_count"] == 1
