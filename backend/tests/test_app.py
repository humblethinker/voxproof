from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import sqlite3

import httpx
from fastapi.testclient import TestClient
from fastapi import HTTPException
import pytest
from backend.app import create_app, initialize, signed_url

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


def test_completion_and_undo_persist_and_retry_safely(tmp_path):
    path = tmp_path / 'tasks.sqlite3'
    body = payload()
    with TestClient(create_app(path)) as client:
        task = client.post('/api/tasks', json=body, headers=HEADERS).json()
        assert task['completed'] is False
        endpoint = f"/api/tasks/{task['id']}"
        for _ in range(2):
            result = client.patch(endpoint, json={'completed': True}, headers=HEADERS)
            assert result.status_code == 200
            assert result.json() == {**task, 'completed': True}
        # A late retry of the original save must not reset completion.
        assert client.post('/api/tasks', json=body, headers=HEADERS).json()['completed'] is True
    with TestClient(create_app(path)) as client:
        assert client.get('/api/tasks').json() == [{**task, 'completed': True}]
        for _ in range(2):
            assert client.patch(endpoint, json={'completed': False}, headers=HEADERS).json() == task
    with TestClient(create_app(path)) as client:
        assert client.get('/api/tasks').json() == [task]


def test_migrates_existing_database_without_losing_tasks(tmp_path):
    path = tmp_path / 'tasks.sqlite3'
    task_id, request_id = str(uuid4()), str(uuid4())
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL, request_id TEXT NOT NULL UNIQUE)')
        db.execute('INSERT INTO tasks VALUES (?, ?, ?, ?)', (task_id, 'Existing task', '2026-10-24T00:00:00Z', request_id))
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(initialize, [path, path]))
    with TestClient(create_app(path)) as client:
        task = client.get('/api/tasks').json()[0]
        assert task == {'id': task_id, 'title': 'Existing task', 'created_at': '2026-10-24T00:00:00Z', 'request_id': request_id, 'completed': False}
        assert client.patch(f'/api/tasks/{task_id}', json={'completed': True}, headers=HEADERS).status_code == 200
    with TestClient(create_app(path)) as client:
        assert client.get('/api/tasks').json()[0]['completed'] is True


def test_completion_requires_valid_boolean_task_and_local_header(tmp_path):
    with TestClient(create_app(tmp_path / 'tasks.sqlite3')) as client:
        task = client.post('/api/tasks', json=payload(), headers=HEADERS).json()
        endpoint = f"/api/tasks/{task['id']}"
        assert client.patch(endpoint, json={'completed': True}).status_code == 403
        for body in [{}, {'completed': 'false'}, {'completed': 1}, {'completed': None}, {'completed': True, 'title': 'Changed'}]:
            assert client.patch(endpoint, json=body, headers=HEADERS).status_code == 422
        assert client.patch('/api/tasks/bad-id', json={'completed': True}, headers=HEADERS).status_code == 422
        assert client.patch(f'/api/tasks/{uuid4()}', json={'completed': True}, headers=HEADERS).status_code == 404
        assert client.get('/api/tasks').json() == [task]
