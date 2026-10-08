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
