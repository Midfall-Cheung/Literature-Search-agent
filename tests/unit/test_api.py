from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.schemas.question import CreateProjectRequest, MessageRequest


def make_client(tmp_path: Path) -> AsyncClient:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'business.db'}",
        checkpoint_database_path=tmp_path / "checkpoints.db",
    )
    app = create_app(settings)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def answer_current(client: AsyncClient, project_id: str, content: str) -> dict:
    response = await client.post(
        f"/projects/{project_id}/messages", json={"content": content}
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_health(tmp_path: Path) -> None:
    async with make_client(tmp_path) as client:
        assert (await client.get("/health")).json() == {"status": "ok", "phase": "A"}


@pytest.mark.anyio
async def test_full_six_round_clarification_and_confirmation(tmp_path: Path) -> None:
    async with make_client(tmp_path) as client:
        response = await client.post(
            "/projects", json={"original_question": "帮我查一下人工智能相关研究"}
        )
        assert response.status_code == 201
        state = response.json()
        project_id = state["project_id"]
        assert state["status"] == "clarifying"
        assert state["current_question"]["target_fields"] == ["population_or_object"]

        for answer in (
            "高校教师",
            "生成式人工智能",
            "教学效率、教学质量",
            "实证研究",
            "2020—2026",
            "中文和英文",
        ):
            state = await answer_current(client, project_id, answer)

        assert state["status"] == "awaiting_confirmation"
        assert state["clarification_round"] == 6
        assert "高校教师" in state["summary"]
        assert state["question_spec"]["languages"] == ["zh", "en"]

        confirmed = await client.post(
            f"/projects/{project_id}/confirm-question", json={"accepted": True}
        )
        assert confirmed.status_code == 200
        final_state = confirmed.json()
        assert final_state["status"] == "confirmed"
        assert final_state["question_spec"]["confirmed"] is True
        assert final_state["missing_fields"] == []


@pytest.mark.anyio
async def test_revision_returns_to_confirmation_with_updated_field(tmp_path: Path) -> None:
    async with make_client(tmp_path) as client:
        response = await client.post(
            "/projects",
            json={
                "original_question": (
                    "对象：高校教师；核心概念：生成式人工智能；结果：教学效率；"
                    "系统综述，2020-2026，中英文"
                )
            },
        )
        state = response.json()
        assert state["status"] == "awaiting_confirmation"
        project_id = state["project_id"]

        response = await client.post(
            f"/projects/{project_id}/confirm-question",
            json={
                "accepted": False,
                "feedback": "把对象改为大学生",
                "field_updates": {"population_or_object": "大学生"},
            },
        )
        assert response.status_code == 200, response.text
        revised = response.json()
        assert revised["status"] == "awaiting_confirmation"
        assert revised["question_spec"]["population_or_object"] == "大学生"
        assert revised["question_spec"]["confirmed"] is False


@pytest.mark.anyio
async def test_state_is_persisted_and_messages_are_auditable(tmp_path: Path) -> None:
    async with make_client(tmp_path) as client:
        created = (await client.post(
            "/projects", json={"original_question": "帮我研究数字教育"}
        )).json()
        project_id = created["project_id"]
        await answer_current(client, project_id, "中学生")
        fetched = await client.get(f"/projects/{project_id}/state")
        assert fetched.status_code == 200
        state = fetched.json()
        assert state["question_spec"]["population_or_object"] == "中学生"
        assert [message["role"] for message in state["messages"]] == [
            "user",
            "assistant",
            "user",
            "assistant",
        ]
        assert state["messages"][1]["target_fields"] == ["population_or_object"]


@pytest.mark.anyio
async def test_invalid_transition_and_unknown_project(tmp_path: Path) -> None:
    async with make_client(tmp_path) as client:
        unknown = await client.get("/projects/00000000-0000-0000-0000-000000000000/state")
        assert unknown.status_code == 404

        created = (await client.post(
            "/projects",
            json={
                "original_question": (
                    "对象：教师；核心概念：AI；结果：效率；系统综述，2020-2026，中英文"
                )
            },
        )).json()
        response = await client.post(
            f"/projects/{created['project_id']}/messages", json={"content": "额外回答"}
        )
        assert response.status_code == 409


def test_checkpoint_can_resume_after_application_recreation(tmp_path: Path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'business.db'}",
        checkpoint_database_path=tmp_path / "checkpoints.db",
    )
    first_service = create_app(settings).state.project_service
    created = first_service.create_project(
        CreateProjectRequest(original_question="帮我研究人工智能")
    )

    recreated_service = create_app(settings).state.project_service
    resumed = recreated_service.answer(
        str(created.project_id), MessageRequest(content="高校教师")
    )

    assert resumed.clarification_round == 1
    assert resumed.question_spec.population_or_object == "高校教师"
