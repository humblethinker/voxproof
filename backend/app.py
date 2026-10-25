"""Local VoiceTasks API: signed voice sessions and explicitly confirmed tasks."""
import os
import sqlite3
from contextlib import asynccontextmanager, closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID, uuid4

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, ConfigDict, field_validator

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')


class TaskInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str
    request_id: UUID

    @field_validator('title')
    @classmethod
    def valid_title(cls, title: str) -> str:
        title = ' '.join(title.split())
        if not 1 <= len(title) <= 200:
            raise ValueError('Use a task title between 1 and 200 characters.')
        return title


def connect(path: Path):
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    return db


def initialize(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(path)) as db, db:
        db.execute('''CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL,
            request_id TEXT NOT NULL UNIQUE
        )''')


async def signed_url() -> str:
    key = os.getenv('ELEVENLABS_API_KEY', '')
    agent_id = os.getenv('ELEVENLABS_AGENT_ID', '')
    if not key or not agent_id:
        raise HTTPException(503, 'Set the ElevenLabs API key and VoiceTasks Agent ID in .env.')
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            result = await client.get(
                'https://api.elevenlabs.io/v1/convai/conversation/get-signed-url',
                params={'agent_id': agent_id}, headers={'xi-api-key': key})
            result.raise_for_status()
            url = result.json()['signed_url']
            parsed = urlparse(url)
            if parsed.scheme != 'wss' or parsed.hostname not in {'api.elevenlabs.io', 'api.eu.elevenlabs.io', 'api.us.elevenlabs.io', 'api.in.elevenlabs.io'}:
                raise ValueError('Unexpected URL')
            return url
    except httpx.HTTPStatusError as exc:
        message = 'ElevenLabs rejected access. Check the Agent ID and API key permissions.' if exc.response.status_code in (401, 403) else 'ElevenLabs could not start a session. Try again shortly.'
        raise HTTPException(502, message) from None
    except (httpx.HTTPError, KeyError, ValueError, TypeError):
        raise HTTPException(502, 'Could not reach ElevenLabs. Check your connection and try again.') from None


def create_app(db_path: Path | None = None, session_provider=signed_url):
    path = db_path or Path(os.getenv('VOICETASKS_DB', str(ROOT / '.local' / 'tasks.sqlite3')))

    @asynccontextmanager
    async def lifespan(app):
        initialize(path)
        yield

    app = FastAPI(title='VoiceTasks', lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost', '127.0.0.1', 'testserver'])

    def require_local_client(value):
        # A custom header requires a cross-origin preflight. We deliberately
        # don't enable CORS: this local app is accessed through the Vite proxy.
        if value != '1':
            raise HTTPException(403, 'Open VoiceTasks through its local web interface.')

    @app.get('/api/voice-session')
    async def voice_session(response: Response, x_voicetasks: str | None = Header(default=None)):
        require_local_client(x_voicetasks)
        response.headers['Cache-Control'] = 'no-store'
        return {'signed_url': await session_provider()}

    @app.get('/api/tasks')
    def list_tasks(response: Response):
        response.headers['Cache-Control'] = 'no-store'
        with closing(connect(path)) as db:
            return [dict(row) for row in db.execute('SELECT * FROM tasks ORDER BY created_at DESC, id DESC')]

    @app.post('/api/tasks')
    def save_task(task: TaskInput, response: Response, x_voicetasks: str | None = Header(default=None)):
        require_local_client(x_voicetasks)
        with closing(connect(path)) as db, db:
            inserted = db.execute('INSERT INTO tasks (id, title, created_at, request_id) VALUES (?, ?, ?, ?) ON CONFLICT(request_id) DO NOTHING',
                (str(uuid4()), task.title, datetime.now(timezone.utc).isoformat(), str(task.request_id))).rowcount
            row = db.execute('SELECT * FROM tasks WHERE request_id = ?', (str(task.request_id),)).fetchone()
            if row['title'] != task.title:
                raise HTTPException(409, 'This request already saved a different title. Reload to see the saved task.')
            response.status_code = 201 if inserted else 200
            return dict(row)

    return app


app = create_app()
