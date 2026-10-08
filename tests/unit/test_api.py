import sqlite3
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
        projects_root=tmp_path / "projects",
    )
    app = create_app(settings)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def persisted_snapshot(tmp_path: Path) -> tuple[str, str]:
    dumps = []
    for name in ("business.db", "checkpoints.db"):
        with sqlite3.connect(tmp_path / name) as db:
            dumps.append("\n".join(db.iterdump()))
    return tuple(dumps)


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
        assert (await client.get("/health")).json() == {
            "status": "ok",
            "phases": "A,B,C,D,E",
        }


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
        projects_root=tmp_path / "projects",
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


@pytest.mark.anyio
async def test_invalid_answer_and_revision_preserve_state_and_resume(tmp_path: Path):
    async with make_client(tmp_path) as client:
        state = (await client.post('/projects', json={'original_question': '大语言模型能否改善高校教师的文献检索效率？'})).json()
        project_id = state['project_id']
        answers = {'population_or_object': '高校教师', 'intervention_or_exposure': '大语言模型', 'outcomes': '文献检索效率', 'study_types': '不限', 'date_from': '2025—2026', 'languages': '中英文'}
        while state['status'] == 'clarifying':
            target = state['current_question']['target_fields'][0]
            if target == 'languages':
                before = state
                persisted = persisted_snapshot(tmp_path)
                bad = await client.post(f'/projects/{project_id}/messages', json={'content': '[2025,2026]'})
                assert bad.status_code == 409
                assert '语言' in bad.json()['detail']
                state = (await client.get(f'/projects/{project_id}/state')).json()
                assert state == before
                assert persisted_snapshot(tmp_path) == persisted
                # A fresh application must recover the same interrupted checkpoint.
                async with make_client(tmp_path) as recreated:
                    state = await answer_current(recreated, project_id, '中英文')
                break
            state = await answer_current(client, project_id, answers[target])
        assert state['status'] == 'awaiting_confirmation'
        assert state['question_spec']['languages'] == ['zh', 'en']
        assert (state['question_spec']['date_from'], state['question_spec']['date_to']) == (2025, 2026)
        before = state
        persisted = persisted_snapshot(tmp_path)
        for updates in ({'languages': ['2025']}, {'explicitly_unrestricted': ['languages'], 'languages': ['zh']}, {'unknown': 1}):
            bad = await client.post(f'/projects/{project_id}/confirm-question', json={'accepted': False, 'field_updates': updates})
            assert bad.status_code == 409
            assert (await client.get(f'/projects/{project_id}/state')).json() == before
            assert persisted_snapshot(tmp_path) == persisted
        revised = await client.post(f'/projects/{project_id}/confirm-question', json={'accepted': False, 'field_updates': {'outcomes': ['文献检索效率']}})
        assert revised.status_code == 200
        assert revised.json()['question_spec']['confirmed'] is False
        confirmed = await client.post(f'/projects/{project_id}/confirm-question', json={'accepted': True})
        assert confirmed.status_code == 200
        assert confirmed.json()['question_spec']['confirmed'] is True


@pytest.mark.anyio
async def test_invalid_initial_dates_do_not_create_partial_project(tmp_path: Path):
    async with make_client(tmp_path) as client:
        response = await client.post('/projects', json={'original_question': '研究人工智能，2026-2020'})
        assert response.status_code == 409
        with sqlite3.connect(tmp_path / 'business.db') as db:
            for table in ('projects', 'messages', 'question_specs', 'audit_events'):
                assert db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 0


@pytest.mark.anyio
async def test_dirty_historical_record_is_reported_and_never_rewritten(tmp_path: Path):
    import json
    async with make_client(tmp_path) as client:
        state = (await client.post('/projects', json={'original_question': '研究人工智能'})).json()
        project_id = state['project_id']
        dirty = dict(state['question_spec'], languages=['2025', '2026'])
        encoded = json.dumps(dirty)
        with sqlite3.connect(tmp_path / 'business.db') as db:
            db.execute('UPDATE projects SET current_spec=? WHERE id=?', (encoded, project_id))
        response = await client.get(f'/projects/{project_id}/state')
        assert response.status_code == 409
        assert '历史' in response.json()['detail']
        bad = await client.post(f'/projects/{project_id}/messages', json={'content': '高校教师'})
        assert bad.status_code == 409
        with sqlite3.connect(tmp_path / 'business.db') as db:
            assert db.execute('SELECT current_spec FROM projects WHERE id=?', (project_id,)).fetchone()[0] == encoded
        clean = (await client.post('/projects', json={'original_question': '研究数字教育'})).json()
        assert clean['question_spec']['languages'] == []


@pytest.mark.anyio
async def test_invalid_explicit_message_updates_do_not_advance_checkpoint(tmp_path: Path):
    async with make_client(tmp_path) as client:
        state = (await client.post('/projects', json={'original_question': '研究人工智能'})).json()
        project_id = state['project_id']
        before = persisted_snapshot(tmp_path)
        for updates in ({'date_from': '2020'}, {'languages': ['2025']}, {'outcomes': [1]}, {'explicitly_unrestricted': 'languages'}, {'confirmed': True}):
            response = await client.post(f'/projects/{project_id}/messages', json={'content': '修改', 'field_updates': updates})
            assert response.status_code == 409
            assert persisted_snapshot(tmp_path) == before
        async with make_client(tmp_path) as recreated:
            resumed = await answer_current(recreated, project_id, '高校教师')
        assert resumed['clarification_round'] == 1
        assert resumed['question_spec']['population_or_object'] == '高校教师'


@pytest.mark.anyio
@pytest.mark.parametrize('answer,expected', [
    ('只要中文，不纳入英文文献', ['zh']),
    ('我想检索中文和英文文献', ['zh', 'en']),
])
async def test_language_prose_answers_and_invalid_retry(tmp_path: Path, answer, expected):
    async with make_client(tmp_path) as client:
        state = (await client.post('/projects', json={
            'original_question': '对象：教师；核心概念：AI；结果：效率；系统综述，2020-2026'
        })).json()
        project_id = state['project_id']
        assert state['current_question']['target_fields'] == ['languages']
        before = persisted_snapshot(tmp_path)
        for invalid in ('[2025,2026]', '中文,abc', '我想检索中文和abc文献'):
            response = await client.post(f'/projects/{project_id}/messages', json={'content': invalid})
            assert response.status_code == 409
            assert persisted_snapshot(tmp_path) == before
        updated = await answer_current(client, project_id, answer)
        assert updated['question_spec']['languages'] == expected
        assert updated['question_spec']['date_from'] == 2020
        assert updated['question_spec']['date_to'] == 2026
        assert updated['status'] == 'awaiting_confirmation'
