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
        return httpx.Response(response.status_code, content=response.content, headers=response.headers)

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
    restored.selectbox(key='workspace_page').select('阶段 B').run()
    assert not restored.exception
    assert term_workspace(restored, project_id)['server']['status'] == 'not_generated'
    button(restored, '生成检索词表').click().run()
    assert not restored.exception
    ws = term_workspace(restored, project_id)
    assert ws['server']['version'] == 1
    original_id = ws['draft'][0]['term_id']
    edit_term(restored, '高校教师P102')
    button(restored, '保存完整词表').click().run()
    assert not restored.exception
    table = client.get_terms(project_id)
    assert table['version'] == 2
    assert any(row['term_id'] == original_id and row['term'] == '高校教师P102' for row in table['terms'])
    button(restored, '加载查询预览').click().run()
    assert term_workspace(restored, project_id)['preview']['term_set_version'] == 2
    button(restored, '加载版本历史').click().run()
    button(restored, '获取 CSV 导出').click().run()
    assert term_workspace(restored, project_id)['csv'][1][1].startswith(b'\xef\xbb\xbf')
    button(restored, '确认检索词表').click().run()
    assert not restored.exception
    assert client.get_terms(project_id)['status'] == 'confirmed'
    fresh = make_app(client)
    fresh.text_input(key='load_project_id').input(project_id)
    button(fresh, '加载项目').click().run()
    fresh.selectbox(key='workspace_page').select('阶段 B').run()
    assert term_workspace(fresh, project_id)['server']['status'] == 'confirmed'


def term_table(project_id='project-1', version=1, status='draft'):
    from uuid import uuid4
    return {'project_id': project_id, 'version': version, 'status': status, 'concepts': [{'concept_id': 'a'}, {'concept_id': 'b'}], 'terms': [
        {'term_id': str(uuid4()), 'concept_id': concept, 'concept_name': name, 'term': name, 'language': 'zh', 'term_type': 'preferred', 'source': 'heuristic', 'field_hint': 'title_abstract', 'enabled': True, 'notes': '', 'normalized_term': name}
        for concept, name in [('a', '教师'), ('b', '人工智能')]
    ]}


class FakeTermsClient(FakeClient):
    def __init__(self, table=None):
        initial = state('confirmed')
        initial['question_spec']['confirmed'] = True
        super().__init__(initial)
        self.tables = {'project-1': table or term_table()}
        self.failures = {}
    def term_call(self, method, *args):
        self.calls.append((method, args))
        if method in self.failures:
            raise self.failures[method]
    def get_terms(self, project_id):
        self.term_call('terms_get', project_id)
        return deepcopy(self.tables[project_id])
    def generate_terms(self, project_id, replace_existing=False):
        self.term_call('terms_generate', project_id, replace_existing)
        self.tables[project_id] = term_table(project_id, self.tables[project_id]['version'] + 1)
        return deepcopy(self.tables[project_id])
    def replace_terms(self, project_id, expected_version, terms):
        from uuid import uuid4
        self.term_call('terms_save', project_id, expected_version, terms)
        table = self.tables[project_id]
        if expected_version != table['version']:
            raise APIError('词表版本冲突', 409)
        table['version'] += 1
        table['status'] = 'draft'
        table['terms'] = [dict(row, term_id=row.get('term_id', str(uuid4()))) for row in terms]
        return deepcopy(table)
    def confirm_terms(self, project_id, expected_version):
        self.term_call('terms_confirm', project_id, expected_version)
        self.tables[project_id]['status'] = 'confirmed'
        return deepcopy(self.tables[project_id])
    def get_query_preview(self, project_id):
        self.term_call('terms_preview', project_id)
        return {'term_set_version': self.tables[project_id]['version'], 'variants': [{'purpose': 'broad', 'canonical_query': 'BACKEND_QUERY', 'included_concept_ids': ['a', 'b'], 'notes': '后端预览'}]}
    def get_term_history(self, project_id):
        self.term_call('terms_history', project_id)
        return {'versions': [{'version': 1, 'status': 'draft', 'created_at': '2026-01-01', 'snapshot': []}]}
    def export_terms_csv(self, project_id):
        self.term_call('terms_csv', project_id)
        return 'terms.csv', '\ufeffterm\n教师'.encode('utf-8')


def open_terms(client):
    at = make_app(client, loaded=True)
    at.selectbox(key='workspace_page').select('阶段 B').run()
    assert not at.exception
    return at


def edit_term(at, value):
    next(item for item in at.text_input if item.label == '术语').input(value).run()
    assert not at.exception


def term_workspace(at, project_id='project-1'):
    return at.session_state['term_workspaces'][project_id]


def test_b_gate_and_get_never_generates():
    client = FakeTermsClient()
    client.state['question_spec']['confirmed'] = False
    at = open_terms(client)
    assert not client.calls
    assert '生成检索词表' not in [item.label for item in at.button]
    client.state['question_spec']['confirmed'] = True
    client.tables['project-1'] = {'project_id': 'project-1', 'version': 0, 'status': 'not_generated', 'terms': [], 'concepts': []}
    at = open_terms(client)
    at.run()
    assert [call[0] for call in client.calls] == ['terms_get']
    button(at, '生成检索词表').click().run()
    assert not at.exception
    assert term_workspace(at)['server']['version'] == 1
    at.run()
    assert len([call for call in client.calls if call[0] == 'terms_generate']) == 1


def test_b_edit_add_remove_save_keeps_ids_sources_and_version():
    client = FakeTermsClient()
    original_id = client.tables['project-1']['terms'][0]['term_id']
    at = open_terms(client)
    edit_term(at, '高校教师')
    assert button(at, '确认检索词表').disabled
    at.selectbox(key='workspace_page').select('阶段 A').run()
    at.selectbox(key='workspace_page').select('阶段 B').run()
    assert term_workspace(at)['draft'][0]['term'] == '高校教师'
    button(at, '增加术语').click().run()
    edit_term(at, '大学教师')
    button(at, '保存完整词表').click().run()
    assert not at.exception
    saved_call = next(call for call in client.calls if call[0] == 'terms_save')
    project_id, version, rows = saved_call[1]
    assert version == 1 and len(rows) == 3
    assert rows[0]['term_id'] == original_id and rows[0]['source'] == 'heuristic'
    assert rows[-1]['source'] == 'user' and 'term_id' not in rows[-1]
    assert all(not {'_key', 'normalized_term', 'created_at', 'updated_at'} & row.keys() for row in rows)
    assert term_workspace(at)['server']['version'] == 2
    assert not term_workspace(at)['dirty']
    button(at, '移除选中术语').click().run()
    button(at, '保存完整词表').click().run()
    assert len(term_workspace(at)['server']['terms']) == 2
    at.run()
    assert len([call for call in client.calls if call[0] == 'terms_save']) == 2


@pytest.mark.parametrize('failure', [APIError('版本冲突', 409), APIError('字段校验失败', 422), APIError('请求超时，先刷新检查')])
def test_b_failed_save_preserves_draft_and_no_implicit_retry(failure):
    client = FakeTermsClient()
    at = open_terms(client)
    edit_term(at, '我的草稿')
    client.failures['terms_save'] = failure
    button(at, '保存完整词表').click().run()
    assert not at.exception
    assert term_workspace(at)['draft'][0]['term'] == '我的草稿'
    assert term_workspace(at)['base_version'] == 1
    at.run()
    assert len([call for call in client.calls if call[0] == 'terms_save']) == 1
    client.tables['project-1']['version'] = 2
    button(at, '刷新服务器词表（保留草稿）').click().run()
    assert term_workspace(at)['draft'][0]['term'] == '我的草稿'
    assert term_workspace(at)['server']['version'] == 2
    assert term_workspace(at)['base_version'] == 1
    assert button(at, '保存完整词表').disabled
    assert any('刷新/比较' in item.value or '版本冲突' in item.value for item in list(at.error) + list(at.warning))
    next(item for item in at.checkbox if '已比较最新服务器' in item.label).check().run()
    button(at, '保留草稿并采用最新版本号').click().run()
    client.failures.clear()
    button(at, '保存完整词表').click().run()
    assert not at.exception
    assert term_workspace(at)['server']['version'] == 3


def test_b_preview_history_csv_independent_and_confirm_reload():
    client = FakeTermsClient()
    at = open_terms(client)
    edit_term(at, '未保存术语')
    button(at, '加载查询预览').click().run()
    assert term_workspace(at)['preview']['term_set_version'] == 1
    assert any('未保存修改' in item.value for item in at.warning)
    client.failures['terms_preview'] = APIError('预览失败', 422)
    button(at, '加载查询预览').click().run()
    assert term_workspace(at)['draft'][0]['term'] == '未保存术语'
    button(at, '加载版本历史').click().run()
    button(at, '获取 CSV 导出').click().run()
    assert not at.exception
    assert term_workspace(at)['history']['versions'][0]['version'] == 1
    assert term_workspace(at)['csv'][1][1].startswith(b'\xef\xbb\xbf')
    assert button(at, '确认检索词表').disabled
    button(at, '保存完整词表').click().run()
    button(at, '确认检索词表').click().run()
    assert term_workspace(at)['server']['status'] == 'confirmed'
    at.run()
    assert len([call for call in client.calls if call[0] == 'terms_confirm']) == 1
    reloaded = open_terms(client)
    assert term_workspace(reloaded)['server']['status'] == 'confirmed'
    assert '术语' not in [item.label for item in reloaded.text_input]


def test_b_two_concepts_gate_and_regeneration_requires_consent():
    client = FakeTermsClient()
    client.tables['project-1']['terms'][1]['term_type'] = 'exclusion'
    at = open_terms(client)
    assert button(at, '确认检索词表').disabled
    assert button(at, '重新生成并覆盖词表').disabled
    at.run()
    assert not any(call[0] == 'terms_generate' for call in client.calls)
    next(item for item in at.checkbox if '确认重新生成' in item.label).check().run()
    button(at, '重新生成并覆盖词表').click().run()
    assert not at.exception
    assert ('terms_generate', ('project-1', True)) in client.calls


def test_b_projects_do_not_share_drafts():
    client = FakeTermsClient()
    at = open_terms(client)
    edit_term(at, '项目一草稿')
    client.state['project_id'] = 'project-2'
    client.tables['project-2'] = term_table('project-2')
    at.text_input(key='load_project_id').input('project-2')
    button(at, '加载项目').click().run()
    assert not at.exception
    edit_term(at, '项目二草稿')
    assert term_workspace(at, 'project-1')['draft'][0]['term'] == '项目一草稿'
    assert term_workspace(at, 'project-2')['draft'][0]['term'] == '项目二草稿'
    client.state['project_id'] = 'project-1'
    at.text_input(key='load_project_id').input('project-1')
    button(at, '加载项目').click().run()
    assert term_workspace(at)['draft'][0]['term'] == '项目一草稿'


def test_b_local_validation_prevents_empty_or_duplicate_put():
    client = FakeTermsClient()
    at = open_terms(client)
    edit_term(at, '')
    button(at, '保存完整词表').click().run()
    assert not at.exception
    assert not any(call[0] == 'terms_save' for call in client.calls)
    assert term_workspace(at)['dirty']
    button(at, '增加术语').click().run()
    edit_term(at, '教师')
    button(at, '保存完整词表').click().run()
    assert not any(call[0] == 'terms_save' for call in client.calls)


def test_b_regeneration_resets_consent_and_server_error_keeps_state():
    client = FakeTermsClient()
    at = open_terms(client)
    consent = next(item for item in at.checkbox if '确认重新生成' in item.label)
    consent.check().run()
    button(at, '重新生成并覆盖词表').click().run()
    assert not at.exception
    assert button(at, '重新生成并覆盖词表').disabled
    at.run()
    assert len([call for call in client.calls if call[0] == 'terms_generate']) == 1
