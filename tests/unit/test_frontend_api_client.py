import json

import httpx
import pytest

from frontend.api_client import APIClient, APIError


def test_all_phase_a_requests_and_payloads(monkeypatch):
    monkeypatch.setenv('LITERATURE_API_BASE_URL', 'http://backend.test/api/')
    requests = []

    def handle(request):
        requests.append((request.method, request.url.path, json.loads(request.content) if request.content else None))
        return httpx.Response(200, json={'project_id': 'project-1', 'status': 'clarifying', 'question_spec': {}})

    client = APIClient(transport=httpx.MockTransport(handle))
    assert client.health()['status'] == 'clarifying'
    assert client.create_project('研究问题')['project_id'] == 'project-1'
    client.get_state('project-1')
    client.answer('project-1', '2020—2026')
    client.answer('project-1', '我想检索中文和英文文献')
    client.answer('project-1', '修订', {'outcomes': ['效率']})
    client.confirm('project-1')
    client.revise('project-1', {'languages': ['zh', 'en']}, '增加英文')
    client.revise('project-1', {'date_from': 2020})
    client.revise('project-1', {'date_to': None})
    client.revise('project-1', {'explicitly_unrestricted': ['date_to']})
    assert requests == [
        ('GET', '/api/health', None),
        ('POST', '/api/projects', {'original_question': '研究问题'}),
        ('GET', '/api/projects/project-1/state', None),
        ('POST', '/api/projects/project-1/messages', {'content': '2020—2026'}),
        ('POST', '/api/projects/project-1/messages', {'content': '我想检索中文和英文文献'}),
        ('POST', '/api/projects/project-1/messages', {'content': '修订', 'field_updates': {'outcomes': ['效率']}}),
        ('POST', '/api/projects/project-1/confirm-question', {'accepted': True}),
        ('POST', '/api/projects/project-1/confirm-question', {'accepted': False, 'field_updates': {'languages': ['zh', 'en']}, 'feedback': '增加英文'}),
        ('POST', '/api/projects/project-1/confirm-question', {'accepted': False, 'field_updates': {'date_from': 2020}}),
        ('POST', '/api/projects/project-1/confirm-question', {'accepted': False, 'field_updates': {'date_to': None}}),
        ('POST', '/api/projects/project-1/confirm-question', {'accepted': False, 'field_updates': {'explicitly_unrestricted': ['date_to']}}),
    ]


@pytest.mark.parametrize('status,detail,expected', [
    (400, '请求无效', '请求无效'), (404, None, '项目不存在'),
    (409, '当前问题需要填写语言', '当前问题需要填写语言'),
    (422, [{'loc': ['body', 'content'], 'msg': '字段不能为空', 'input': '敏感研究问题'}], 'content：字段不能为空'),
    (500, None, '后端服务暂时不可用'),
])
def test_http_errors_are_readable(status, detail, expected):
    client = APIClient(transport=httpx.MockTransport(lambda request: httpx.Response(status, json={'detail': detail})))
    with pytest.raises(APIError, match=expected) as captured:
        client.get_state('project-1')
    assert captured.value.status_code == status
    assert '敏感研究问题' not in str(captured.value)


@pytest.mark.parametrize('exception,expected', [(httpx.ConnectError, '无法连接后端'), (httpx.ReadTimeout, '请求超时')])
def test_network_failures(exception, expected):
    def handle(request):
        raise exception('secret URL and content', request=request)
    client = APIClient(transport=httpx.MockTransport(handle))
    with pytest.raises(APIError, match=expected) as captured:
        client.create_project('研究问题')
    assert 'secret' not in str(captured.value)


@pytest.mark.parametrize('response', [httpx.Response(200, text='not JSON'), httpx.Response(200, json=[]), httpx.Response(503, text='unavailable')])
def test_bad_responses(response):
    client = APIClient(transport=httpx.MockTransport(lambda request: response))
    with pytest.raises(APIError):
        client.health()


def test_default_url(monkeypatch):
    monkeypatch.delenv('LITERATURE_API_BASE_URL', raising=False)
    assert APIClient().base_url == 'http://127.0.0.1:8000/'


def test_incomplete_project_response():
    client = APIClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})))
    with pytest.raises(APIError, match='后端项目响应不完整'):
        client.get_state('project-1')


def test_phase_b_response_kinds_requests_and_binary_export():
    project_id = '00000000-0000-0000-0000-000000000001'
    calls = []
    content = '\ufeffterm\r\n教师\r\n'.encode('utf-8')
    def handle(request):
        calls.append((request.method, request.url.path, json.loads(request.content) if request.content else None))
        if request.url.path.endswith('export.csv'):
            return httpx.Response(200, content=content, headers={'Content-Disposition': 'attachment; filename="terms_v1.csv"'})
        if request.url.path.endswith('/history'):
            data = {'project_id': project_id, 'versions': []}
        elif request.url.path.endswith('/query-preview'):
            data = {'project_id': project_id, 'term_set_version': 1, 'variants': []}
        else:
            data = {'project_id': project_id, 'status': 'draft', 'version': 1, 'terms': [], 'concepts': []}
        return httpx.Response(200, json=data)
    client = APIClient(transport=httpx.MockTransport(handle))
    assert client.get_terms(project_id)['status'] == 'draft'
    client.generate_terms(project_id)
    client.generate_terms(project_id, True)
    client.replace_terms(project_id, 1, [])
    assert client.get_query_preview(project_id)['term_set_version'] == 1
    assert client.get_term_history(project_id)['versions'] == []
    client.confirm_terms(project_id, 2)
    assert client.export_terms_csv(project_id) == ('terms_v1.csv', content)
    assert calls[1][2] == {'replace_existing': False}
    assert calls[2][2] == {'replace_existing': True}
    assert calls[3][0] == 'PUT' and calls[3][2] == {'expected_version': 1, 'terms': []}
    assert calls[6][2] == {'expected_version': 2}
    assert calls[7][1].endswith('/terms/export.csv')


@pytest.mark.parametrize('kind', ['terms', 'preview', 'history'])
def test_invalid_b_response(kind):
    client = APIClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})))
    with pytest.raises(APIError, match='词表响应格式异常'):
        client._request('GET', 'test', response_kind=kind)


def test_csv_error_does_not_attempt_download():
    client = APIClient(transport=httpx.MockTransport(lambda request: httpx.Response(409, json={'detail': '词表版本冲突'})))
    with pytest.raises(APIError, match='版本冲突'):
        client.export_terms_csv('project-1')


def test_csv_success_never_uses_json_and_unsafe_filename_falls_back():
    class CSVResponse(httpx.Response):
        def json(self, **kwargs):
            raise AssertionError('CSV must not be decoded as JSON')
    response = CSVResponse(200, content=b'\xef\xbb\xbfdata', headers={'Content-Disposition': 'attachment; filename="bad?token=x.csv"'})
    client = APIClient(transport=httpx.MockTransport(lambda request: response))
    assert client.export_terms_csv('project-1') == ('terms.csv', b'\xef\xbb\xbfdata')
