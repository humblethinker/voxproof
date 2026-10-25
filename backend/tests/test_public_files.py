from scripts.check_public_files import violations


def test_blocks_private_files_even_without_credentials():
    for name in ['.env', '.env.production', 'web/.env.local', '.local/tasks.sqlite3',
                 'recording.wav', 'private.pem', 'web/dist/assets/bundle.js', '.venv/config']:
        assert violations(name, b'')


def test_environment_example_must_be_blank():
    assert not violations('.env.example', b'ELEVENLABS_API_KEY=\nELEVENLABS_AGENT_ID=\n')
    assert violations('.env.example', b'ELEVENLABS_API_KEY=real-value\n')


def test_credentials_cannot_hide_in_source_files():
    assert violations('web/src/settings.ts', b'sk_' + b'a1' * 24)
    assert violations('README.md', b'-----BEGIN ' + b'PRIVATE KEY-----')
    assert not violations('backend/app.py', b"key = os.getenv('ELEVENLABS_API_KEY')")
