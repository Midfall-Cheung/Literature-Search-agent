import csv
import io
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.schemas.question import ResearchQuestionSpec
from app.schemas.terms import TermSource, TermType
from app.services.term_builder import (
    StructuredLLMTermExpander,
    TermBuilder,
    deduplicate_candidates,
    normalize_term,
)


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


async def create_confirmed_project(client: AsyncClient) -> str:
    created = await client.post(
        "/projects",
        json={
            "original_question": (
                "对象：高校教师；核心概念：生成式人工智能；结果：检索效率；"
                "系统综述，2020-2026，中英文"
            )
        },
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["project_id"]
    confirmed = await client.post(
        f"/projects/{project_id}/confirm-question", json={"accepted": True}
    )
    assert confirmed.status_code == 200, confirmed.text
    return project_id


def editable_terms(table: dict) -> list[dict]:
    fields = {
        "term_id",
        "concept_id",
        "concept_name",
        "term",
        "language",
        "term_type",
        "source",
        "field_hint",
        "enabled",
        "notes",
    }
    return [{key: value for key, value in term.items() if key in fields} for term in table["terms"]]


@pytest.mark.anyio
async def test_terms_require_confirmed_question(tmp_path: Path) -> None:
    async with make_client(tmp_path) as client:
        created = await client.post(
            "/projects", json={"original_question": "帮我研究生成式人工智能"}
        )
        project_id = created.json()["project_id"]
        response = await client.post(
            f"/projects/{project_id}/terms/generate",
            json={"replace_existing": False},
        )
        assert response.status_code == 422
        assert "阶段 A" in response.json()["detail"]


@pytest.mark.anyio
async def test_generate_group_preview_confirm_and_export(tmp_path: Path) -> None:
    async with make_client(tmp_path) as client:
        project_id = await create_confirmed_project(client)
        generated = await client.post(
            f"/projects/{project_id}/terms/generate",
            json={"replace_existing": False},
        )
        assert generated.status_code == 201, generated.text
        table = generated.json()
        assert table["status"] == "draft"
        assert table["version"] == 1
        assert {item["concept_id"] for item in table["concepts"]} == {"C1", "C2", "C3"}
        assert {item["source"] for item in table["terms"]} == {"user", "heuristic"}
        assert any(item["term"] == "generative AI" for item in table["terms"])
        assert any(item["language"] == "zh" for item in table["terms"])
        assert any(item["language"] == "en" for item in table["terms"])

        preview = await client.get(f"/projects/{project_id}/query-preview")
        assert preview.status_code == 200, preview.text
        variants = preview.json()["variants"]
        assert [item["purpose"] for item in variants[:3]] == [
            "broad",
            "focused",
            "exact_phrase",
        ]
        assert " OR " in variants[0]["canonical_query"]
        assert " AND " in variants[0]["canonical_query"]

        confirmed = await client.post(
            f"/projects/{project_id}/confirm-terms",
            json={"expected_version": 1},
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["status"] == "confirmed"
        repeated_confirmation = await client.post(
            f"/projects/{project_id}/confirm-terms",
            json={"expected_version": 1},
        )
        assert repeated_confirmation.status_code == 200
        assert repeated_confirmation.json()["status"] == "confirmed"

        history = await client.get(f"/projects/{project_id}/terms/history")
        assert history.status_code == 200
        assert [item["status"] for item in history.json()["versions"]] == [
            "draft",
            "confirmed",
        ]

        exported = await client.get(f"/projects/{project_id}/terms/export.csv")
        assert exported.status_code == 200
        assert exported.content.startswith(b"\xef\xbb\xbf")
        rows = list(csv.DictReader(io.StringIO(exported.content.decode("utf-8-sig"))))
        assert len(rows) == len(table["terms"])
        assert set(rows[0]) >= {
            "concept_id",
            "term",
            "normalized_term",
            "source",
            "enabled",
        }


@pytest.mark.anyio
async def test_edit_reopens_terms_and_uses_optimistic_version(tmp_path: Path) -> None:
    async with make_client(tmp_path) as client:
        project_id = await create_confirmed_project(client)
        table = (
            await client.post(
                f"/projects/{project_id}/terms/generate",
                json={"replace_existing": False},
            )
        ).json()
        terms = editable_terms(table)
        terms.append(
            {
                "concept_id": "C2",
                "concept_name": "核心干预/暴露/概念",
                "term": "GenAI",
                "language": "en",
                "term_type": "abbreviation",
                "source": "user",
                "field_hint": "title_abstract",
                "enabled": True,
                "notes": "用户补充",
            }
        )
        updated = await client.put(
            f"/projects/{project_id}/terms",
            json={"expected_version": 1, "terms": terms},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["version"] == 2
        assert updated.json()["status"] == "draft"
        assert any(item["term"] == "GenAI" for item in updated.json()["terms"])

        stale = await client.put(
            f"/projects/{project_id}/terms",
            json={"expected_version": 1, "terms": terms},
        )
        assert stale.status_code == 409
        assert "版本冲突" in stale.json()["detail"]

        terms[-1]["term_id"] = "00000000-0000-0000-0000-000000000001"
        foreign_id = await client.put(
            f"/projects/{project_id}/terms",
            json={"expected_version": 2, "terms": terms},
        )
        assert foreign_id.status_code == 422
        assert "当前项目" in foreign_id.json()["detail"]


@pytest.mark.anyio
async def test_confirmation_requires_two_enabled_positive_concepts(tmp_path: Path) -> None:
    async with make_client(tmp_path) as client:
        project_id = await create_confirmed_project(client)
        table = (
            await client.post(
                f"/projects/{project_id}/terms/generate",
                json={"replace_existing": False},
            )
        ).json()
        terms = editable_terms(table)
        for term in terms:
            term["enabled"] = term["concept_id"] == "C1"
        edited = await client.put(
            f"/projects/{project_id}/terms",
            json={"expected_version": 1, "terms": terms},
        )
        assert edited.status_code == 200
        rejected = await client.post(
            f"/projects/{project_id}/confirm-terms",
            json={"expected_version": 2},
        )
        assert rejected.status_code == 422
        assert "两个" in rejected.json()["detail"]


def test_normalization_and_deduplication() -> None:
    spec = ResearchQuestionSpec(
        original_question="研究 AI",
        population_or_object="高校教师",
        intervention_or_exposure="AI",
        outcomes=["效率"],
        confirmed=True,
    )
    terms = TermBuilder().build(spec)
    duplicate = terms[0].model_copy(update={"term": f"  {terms[0].term}  "})
    deduplicated = deduplicate_candidates([terms[0], duplicate])
    assert len(deduplicated) == 1
    assert normalize_term("  ＡＩ  ") == "ai"


class FakeStructuredModel:
    def invoke(self, _: str) -> dict:
        return {
            "terms": [
                {
                    "concept_id": "C2",
                    "term": "foundation model",
                    "language": "en",
                    "term_type": "synonym",
                },
                {
                    "concept_id": "UNKNOWN",
                    "term": "discard me",
                    "language": "en",
                    "term_type": "synonym",
                },
            ]
        }


class FakeChatModel:
    def with_structured_output(self, _: type) -> FakeStructuredModel:
        return FakeStructuredModel()


def test_llm_terms_are_forced_to_llm_provenance_and_known_concepts() -> None:
    spec = ResearchQuestionSpec(
        original_question="研究大语言模型",
        population_or_object="研究人员",
        intervention_or_exposure="大语言模型",
        outcomes=["检索效率"],
        confirmed=True,
    )
    terms = TermBuilder(StructuredLLMTermExpander(FakeChatModel())).build(spec)
    candidate = next(item for item in terms if item.term == "foundation model")
    assert candidate.source == TermSource.LLM
    assert candidate.term_type == TermType.SYNONYM
    assert all(item.concept_id != "UNKNOWN" for item in terms)
