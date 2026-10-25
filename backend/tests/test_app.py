from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient
from fastapi import HTTPException
import pytest
from backend.app import create_app, signed_url

HEADERS = {'X-VoiceTasks': '1'}


def payload(title='Buy groceries and coffee', request_id=None):
    return {'title': title, 'request_id': request_id or str(uuid4())}


def test_persistence_and_normalized_title(tmp_path):
    path = tmp_path / 'tasks.sqlite3'
    with TestClient(create_app(path)) as client:
        assert client.get('/api/tasks').json() == []
        saved = client.post('/api/tasks', json=payload('  Buy  groceries  '), headers=HEADERS)
        assert saved.status_code == 201
        assert saved.json()['title'] == 'Buy groceries'
    with TestClient(create_app(path)) as client:
        assert client.get('/api/tasks').json() == [saved.json()]


def test_retries_and_conflicting_request_id(tmp_path):
    with TestClient(create_app(tmp_path / 'tasks.sqlite3')) as client:
        body = payload()
        first = client.post('/api/tasks', json=body, headers=HEADERS)
        retry = client.post('/api/tasks', json=body, headers=HEADERS)
        assert retry.status_code == 200
        assert first.json() == retry.json()
        conflict = client.post('/api/tasks', json={**body, 'title': 'Different task'}, headers=HEADERS)
        assert conflict.status_code == 409
        assert len(client.get('/api/tasks').json()) == 1


def test_simultaneous_confirmation_saves_once(tmp_path):
    with TestClient(create_app(tmp_path / 'tasks.sqlite3')) as client:
        body = payload()
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(lambda _: client.post('/api/tasks', json=body, headers=HEADERS), range(8)))
        assert [r.status_code for r in responses].count(201) == 1
        assert len({r.json()['id'] for r in responses}) == 1
        assert len(client.get('/api/tasks').json()) == 1


@pytest.mark.parametrize('body', [payload(' '), payload('a' * 201), payload(request_id='bad'), {'title': 1, 'request_id': str(uuid4())}, {**payload(), 'extra': True}])
def test_invalid_tasks_never_save(tmp_path, body):
    with TestClient(create_app(tmp_path / 'tasks.sqlite3')) as client:
        assert client.post('/api/tasks', json=body, headers=HEADERS).status_code == 422
        assert client.get('/api/tasks').json() == []


def test_session_never_returns_api_key_and_does_not_cache(tmp_path):
    async def provider():
        return 'wss://api.elevenlabs.io/test?token=temporary'
    with TestClient(create_app(tmp_path / 'tasks.sqlite3', provider)) as client:
        assert client.get('/api/voice-session').status_code == 403
        result = client.get('/api/voice-session', headers=HEADERS)
        assert result.status_code == 200
        assert result.headers['Cache-Control'] == 'no-store'
        assert set(result.json()) == {'signed_url'}
        assert client.post('/api/tasks', json=payload()).status_code == 403
        assert client.options('/api/tasks', headers={'Origin': 'https://example.com', 'Access-Control-Request-Method': 'POST'}).status_code == 405


def test_vendor_error_is_sanitized(monkeypatch):
    import asyncio
    from backend import app as module
    monkeypatch.setenv('ELEVENLABS_API_KEY', 'secret-key')
    monkeypatch.setenv('ELEVENLABS_AGENT_ID', 'test-agent')
    original = httpx.AsyncClient
    def handler(request):
        assert request.headers['xi-api-key'] == 'secret-key'
        return httpx.Response(401, json={'message': 'secret-key'})
    monkeypatch.setattr(module.httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(signed_url())
    assert 'secret-key' not in str(exc.value)
    assert exc.value.status_code == 502
