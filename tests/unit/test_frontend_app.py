from copy import deepcopy
from pathlib import Path

import pytest

pytest.importorskip('streamlit', reason='安装 .[ui] 后运行 Streamlit UI 测试')
from streamlit.testing.v1 import AppTest

from frontend.api_client import APIError

APP = Path(__file__).resolve().parents[2] / 'frontend/app.py'


def state(status='clarifying'):
    return {
        'project_id': 'project-1', 'status': status, 'clarification_round': 5,
        'max_rounds': 6, 'updated_at': 'v1',
        'current_question': {'prompt': '希望纳入哪些语言？', 'target_fields': ['languages'], 'round_number': 6} if status == 'clarifying' else None,
        'question_spec': {'original_question': '研究问题', 'population_or_object': '教师', 'intervention_or_exposure': 'AI', 'outcomes': ['效率'], 'study_types': [], 'date_from': 2020, 'date_to': 2026, 'languages': ['zh'], 'explicitly_unrestricted': []},
        'messages': [{'role': 'user', 'content': '研究问题'}],
        'summary': '研究对象：教师；关注结果：效率', 'assumptions': ['研究类型不限'],
        'missing_fields': ['languages'] if status == 'clarifying' else [],
    }


class FakeClient:
    def __init__(self, initial=None):
        self.state = initial or state()
        self.calls = []
        self.failure = None

    def _call(self, method, *args):
        self.calls.append((method, args))
        if self.failure:
            raise self.failure

    def health(self):
        self._call('health')
        return {'status': 'ok'}

    def create_project(self, question):
        self._call('create', question)
        return deepcopy(self.state)

    def get_state(self, project_id):
        self._call('get', project_id)
        return deepcopy(self.state)

    def answer(self, project_id, content):
        self._call('answer', project_id, content)
        self.state['status'] = 'awaiting_confirmation'
        self.state['current_question'] = None
        self.state['clarification_round'] = 6
        return deepcopy(self.state)

    def confirm(self, project_id):
        self._call('confirm', project_id)
        self.state['status'] = 'confirmed'
        return deepcopy(self.state)

    def revise(self, project_id, field_updates, feedback):
        self._call('revise', project_id, field_updates, feedback)
        self.state['question_spec'].update(field_updates)
        self.state['updated_at'] = 'v2'
        return deepcopy(self.state)


def button(at, label):
    return next(widget for widget in at.button if widget.label == label)


def textarea(at, label):
    return next(widget for widget in at.text_area if widget.label == label)


def make_app(client, loaded=False):
    at = AppTest.from_file(str(APP), default_timeout=10)
    at.session_state['_api_client'] = client
    if loaded:
        at.session_state['project_state'] = deepcopy(client.state)
        at.session_state['project_id'] = client.state['project_id']
    at.run()
    assert not at.exception
    return at


def test_create_load_refresh_and_reruns_do_not_write():
    client = FakeClient()
    at = make_app(client)
    textarea(at, '原始研究问题').input('大语言模型能否改善检索效率？')
    button(at, '创建项目').click().run()
    assert not at.exception
    assert client.calls == [('create', ('大语言模型能否改善检索效率？',))]
    assert at.session_state['project_id'] == 'project-1'
    assert textarea(at, '原始研究问题').value == ''
    at.run()
    assert len(client.calls) == 1
    at.text_input(key='load_project_id').input('project-1')
    button(at, '加载项目').click().run()
    button(at, '刷新项目状态').click().run()
    assert client.calls[-2:] == [('get', ('project-1',)), ('get', ('project-1',))]


def test_failed_answer_preserves_draft_state_and_allows_retry():
    client = FakeClient()
    at = make_app(client, loaded=True)
    client.failure = APIError('当前问题需要填写语言', 409)
    textarea(at, '你的回答').input('[2025,2026]')
    button(at, '提交回答').click().run()
    assert not at.exception
    assert at.error[0].value == '当前问题需要填写语言'
    assert textarea(at, '你的回答').value == '[2025,2026]'
    assert at.session_state['project_state']['clarification_round'] == 5
    assert at.session_state['project_id'] == 'project-1'
    at.run()
    assert len(client.calls) == 1
    client.failure = None
    textarea(at, '你的回答').input('我想检索中文和英文文献')
    button(at, '提交回答').click().run()
    assert client.calls[-1] == ('answer', ('project-1', '我想检索中文和英文文献'))
    assert at.session_state['project_state']['status'] == 'awaiting_confirmation'
    assert '确认研究问题' in [item.label for item in at.button]
    assert '提交回答' not in [item.label for item in at.button]


@pytest.mark.parametrize('operation', ['create', 'load'])
def test_network_or_404_failure_preserves_existing_project_and_input(operation):
    client = FakeClient()
    at = make_app(client, loaded=True)
    client.failure = APIError('项目不存在' if operation == 'load' else '无法连接后端', 404 if operation == 'load' else None)
    if operation == 'load':
        at.text_input(key='load_project_id').input('missing-id')
        button(at, '加载项目').click().run()
        assert at.text_input(key='load_project_id').value == 'missing-id'
    else:
        textarea(at, '原始研究问题').input('保留的研究问题')
        button(at, '创建项目').click().run()
        assert textarea(at, '原始研究问题').value == '保留的研究问题'
    assert at.session_state['project_id'] == 'project-1'
    assert at.session_state['project_state'] == state()
    assert at.error
    at.run()
    assert len(client.calls) == 1


@pytest.mark.parametrize('field,value,expected', [
    ('outcomes', '检索效率\n检索准确率', ['检索效率', '检索准确率']),
    ('study_types', '系统综述', ['系统综述']),
    ('population_or_object', '大学生', '大学生'),
    ('intervention_or_exposure', '大语言模型', '大语言模型'),
    ('languages', ['zh', 'en'], ['zh', 'en']),
    ('date_from', 2021, 2021), ('date_to', 2025, 2025),
])
def test_typed_revisions_and_confirmation(field, value, expected):
    client = FakeClient(state('awaiting_confirmation'))
    at = make_app(client, loaded=True)
    at.selectbox(key='revision_field').select(field).run()
    if field == 'languages':
        at.multiselect[0].set_value(value)
    elif field.startswith('date_'):
        at.number_input[0].set_value(value)
    elif field in ('outcomes', 'study_types'):
        textarea(at, '修订值（每行一项，也可用逗号分隔）').input(value)
    else:
        next(widget for widget in at.text_input if widget.label == '修订值').input(value)
    button(at, '提交修订').click().run()
    assert not at.exception
    assert client.calls[-2:] == [('revise', ('project-1', {field: expected}, None)), ('get', ('project-1',))]
    assert at.session_state['project_state']['question_spec'][field] == expected
    button(at, '确认研究问题').click().run()
    assert not at.exception
    assert client.calls[-1] == ('confirm', ('project-1',))
    assert at.session_state['project_state']['status'] == 'confirmed'
    assert '提交修订' not in [item.label for item in at.button]
    assert '确认研究问题' not in [item.label for item in at.button]
    assert any('阶段 A 已确认' in message.value for message in at.success)
    assert any('P1-02' in message.value for message in at.info)
    assert '加载项目' in [item.label for item in at.button]


@pytest.mark.parametrize('mode,expected', [
    ('未指定（空值）', {'date_from': None}),
    ('不限', {'explicitly_unrestricted': ['date_from']}),
])
def test_year_null_is_distinct_from_unrestricted(mode, expected):
    client = FakeClient(state('awaiting_confirmation'))
    at = make_app(client, loaded=True)
    at.selectbox(key='revision_field').select('date_from').run()
    at.radio[0].set_value(mode).run()
    button(at, '提交修订').click().run()
    assert not at.exception
    assert client.calls[-2] == ('revise', ('project-1', expected, None))


def test_failed_revision_and_busy_controls():
    client = FakeClient(state('awaiting_confirmation'))
    at = make_app(client, loaded=True)
    at.selectbox(key='revision_field').select('outcomes').run()
    textarea(at, '修订值（每行一项，也可用逗号分隔）').input('准确率')
    client.failure = APIError('字段类型错误', 422)
    button(at, '提交修订').click().run()
    assert at.error[0].value == '字段类型错误'
    assert textarea(at, '修订值（每行一项，也可用逗号分隔）').value == '准确率'
    at.run()
    assert len(client.calls) == 1
    at.session_state['busy'] = True
    at.run()
    assert all(item.disabled for item in at.button)
    assert len(client.calls) == 1


def test_phase_a_end_to_end_against_temporary_api(tmp_path):
    """Exercise the rendered UI through the HTTP client and real Phase A routes."""
    import httpx
    import asyncio
    from app.config import Settings
    from app.main import create_app
    from frontend.api_client import APIClient

    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'business.db'}",
        checkpoint_database_path=tmp_path / 'checkpoints.db',
        projects_root=tmp_path / 'projects',
    )
    backend = create_app(settings)
    def handle(request):
        async def send():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=backend), base_url="http://test"
            ) as api:
                return await api.request(
                    request.method, request.url.path, content=request.content,
                    headers={"content-type": "application/json"},
                )
        response = asyncio.run(send())
        return httpx.Response(response.status_code, json=response.json())

    client = APIClient(transport=httpx.MockTransport(handle))
    at = make_app(client)
    textarea(at, '原始研究问题').input('大语言模型能否改善高校教师的文献检索效率？')
    button(at, '创建项目').click().run()
    project_id = at.session_state['project_id']
    answers = {
        'population_or_object': '高校教师',
        'intervention_or_exposure': '大语言模型',
        'outcomes': '检索效率', 'study_types': '不限',
        'date_from': '2020—2026', 'languages': '我想检索中文和英文文献',
    }
    for _ in range(6):
        if at.session_state['project_state']['status'] != 'clarifying':
            break
        before = deepcopy(at.session_state['project_state'])
        field = before['current_question']['target_fields'][0]
        if field == 'languages':
            textarea(at, '你的回答').input('[2025,2026]')
            button(at, '提交回答').click().run()
            assert at.error and '语言' in at.error[0].value
            assert at.session_state['project_state'] == before
            assert client.get_state(project_id) == before
            assert textarea(at, '你的回答').value == '[2025,2026]'
        textarea(at, '你的回答').input(answers[field])
        button(at, '提交回答').click().run()
        assert not at.exception and not at.error
    assert at.session_state['project_state']['status'] == 'awaiting_confirmation'
    at.selectbox(key='revision_field').select('outcomes').run()
    textarea(at, '修订值（每行一项，也可用逗号分隔）').input('检索效率\n检索准确率')
    button(at, '提交修订').click().run()
    state = client.get_state(project_id)
    assert state['question_spec']['outcomes'] == ['检索效率', '检索准确率']
    assert state['question_spec']['languages'] == ['zh', 'en']
    assert (state['question_spec']['date_from'], state['question_spec']['date_to']) == (2020, 2026)
    assert state['status'] == 'awaiting_confirmation'
    button(at, '确认研究问题').click().run()
    assert not at.exception and not at.error
    assert at.session_state['project_state']['status'] == 'confirmed'
    restored = make_app(client)
    restored.text_input(key='load_project_id').input(project_id)
    button(restored, '加载项目').click().run()
    assert not restored.exception
    assert restored.session_state['project_state'] == client.get_state(project_id)
