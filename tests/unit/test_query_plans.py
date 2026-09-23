from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def make_client(tmp_path: Path) -> AsyncClient:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'business.db'}",
        checkpoint_database_path=tmp_path / "checkpoints.db",
    )
    return AsyncClient(
        transport=ASGITransport(app=create_app(settings)), base_url="http://test"
    )


async def create_confirmed_terms(client: AsyncClient) -> str:
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
    confirmed_question = await client.post(
        f"/projects/{project_id}/confirm-question", json={"accepted": True}
    )
    assert confirmed_question.status_code == 200
    terms = await client.post(
        f"/projects/{project_id}/terms/generate",
        json={"replace_existing": False},
    )
    assert terms.status_code == 201
    confirmed_terms = await client.post(
        f"/projects/{project_id}/confirm-terms",
        json={"expected_version": terms.json()["version"]},
    )
    assert confirmed_terms.status_code == 200
    return project_id


@pytest.mark.anyio
async def test_compile_confirm_and_audit_query_plan(tmp_path: Path) -> None:
    async with make_client(tmp_path) as client:
        project_id = await create_confirmed_terms(client)
        response = await client.post(
            f"/projects/{project_id}/query-plans/compile",
            json={
                "providers": ["openalex", "crossref", "semantic_scholar"],
                "page_size": 100,
                "max_records": 500,
                "replace_existing": False,
            },
        )
        assert response.status_code == 201, response.text
        plan = response.json()
        assert plan["status"] == "draft"
        assert plan["version"] == 1
        assert plan["term_set_version"] == 1
        assert len(plan["queries"]) == 9
        assert {item["provider"] for item in plan["queries"]} == {
            "openalex",
            "crossref",
            "semantic_scholar",
        }

        openalex = next(
            item
            for item in plan["queries"]
            if item["provider"] == "openalex" and item["purpose"] == "broad"
        )
        assert " OR " in openalex["provider_query"]
        assert openalex["request_params"]["per_page"] == 100
        assert "from_publication_date:2020-01-01" in openalex["request_params"]["filter"]

        crossref = next(
            item for item in plan["queries"] if item["provider"] == "crossref"
        )
        assert " AND " not in crossref["provider_query"]
        assert "from-pub-date:2020-01-01" in crossref["request_params"]["filter"]

        semantic = next(
            item
            for item in plan["queries"]
            if item["provider"] == "semantic_scholar"
            and item["purpose"] == "broad"
        )
        assert " | " in semantic["provider_query"]
        assert " + " in semantic["provider_query"]
        assert semantic["request_params"]["publicationDateOrYear"] == "2020:2026"
        assert semantic["request_params"]["publicationTypes"] == "Review"

        fetched = await client.get(f"/projects/{project_id}/query-plans")
        assert fetched.status_code == 200
        assert fetched.json()["queries"] == plan["queries"]

        confirmed = await client.post(
            f"/projects/{project_id}/confirm-query-plan",
            json={"expected_version": 1},
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["status"] == "confirmed"

        history = await client.get(f"/projects/{project_id}/query-plans/history")
        assert [item["status"] for item in history.json()["versions"]] == [
            "draft",
            "confirmed",
        ]


@pytest.mark.anyio
async def test_query_plan_requires_confirmed_terms_and_explicit_replace(
    tmp_path: Path,
) -> None:
    async with make_client(tmp_path) as client:
        created = await client.post(
            "/projects",
            json={
                "original_question": (
                    "对象：教师；核心概念：人工智能；结果：效率；"
                    "系统综述，2020-2026，中英文"
                )
            },
        )
        project_id = created.json()["project_id"]
        await client.post(
            f"/projects/{project_id}/confirm-question", json={"accepted": True}
        )
        blocked = await client.post(
            f"/projects/{project_id}/query-plans/compile", json={}
        )
        assert blocked.status_code == 422
        assert "阶段 B" in blocked.json()["detail"]

        terms = await client.post(
            f"/projects/{project_id}/terms/generate",
            json={"replace_existing": False},
        )
        await client.post(
            f"/projects/{project_id}/confirm-terms",
            json={"expected_version": terms.json()["version"]},
        )
        first = await client.post(
            f"/projects/{project_id}/query-plans/compile", json={}
        )
        assert first.status_code == 201
        duplicate = await client.post(
            f"/projects/{project_id}/query-plans/compile", json={}
        )
        assert duplicate.status_code == 409

        replaced = await client.post(
            f"/projects/{project_id}/query-plans/compile",
            json={"providers": ["openalex"], "replace_existing": True},
        )
        assert replaced.status_code == 201
        assert replaced.json()["version"] == 2
        assert {item["provider"] for item in replaced.json()["queries"]} == {
            "openalex"
        }

        stale = await client.post(
            f"/projects/{project_id}/confirm-query-plan",
            json={"expected_version": 1},
        )
        assert stale.status_code == 409
