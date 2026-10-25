"""Create one dedicated private agent; record IDs locally so reruns reuse it."""
import json
import os
from pathlib import Path

import httpx
from dotenv import dotenv_values, set_key

ROOT = Path(__file__).resolve().parents[1]


def main():
    env = ROOT / '.env'
    settings = {**dotenv_values(env), **os.environ}
    key = settings.get('ELEVENLABS_API_KEY')
    if not key:
        raise SystemExit('Set ELEVENLABS_API_KEY in .env first.')
    if settings.get('ELEVENLABS_AGENT_ID'):
        print('An agent is already configured. No new agent created.')
        return
    record = ROOT / '.local' / 'agent-creation.json'
    record.parent.mkdir(exist_ok=True)
    state = json.loads(record.read_text()) if record.exists() else {}

    def remember():
        record.write_text(json.dumps(state, indent=2) + '\n')

    with httpx.Client(base_url='https://api.elevenlabs.io', headers={'xi-api-key': key}, timeout=30) as client:
        def post(path, body):
            response = client.post(path, json=body)
            if response.is_error:
                # No raw request, headers, signed URLs, or response content in logs.
                raise SystemExit(f'ElevenLabs returned HTTP {response.status_code} for {path}. Check Agents read/write permissions and the configuration.')
            return response.json()

        if not state.get('tool_id'):
            tool = json.loads((ROOT / 'agent' / 'tool.json').read_text())
            state['tool_id'] = post('/v1/convai/tools', {'tool_config': tool})['id']
            remember()
        if not state.get('agent_id'):
            config = {
                'name': 'VoiceTasks',
                'conversation_config': {
                    'agent': {
                        'first_message': 'What would you like to add?',
                        'language': 'en',
                        'prompt': {
                            'prompt': (ROOT / 'agent' / 'prompt.txt').read_text(),
                            'llm': 'deepseek-v41-flash',
                            'tool_ids': [state['tool_id']],
                        },
                    },
                    'asr': {'user_input_audio_format': 'pcm_16000'},
                    'tts': {'voice_id': 'cjVigY5qzO86Huf0OWal', 'agent_output_audio_format': 'pcm_16000'},
                    'turn': {'turn_eagerness': 'normal'},
                    'conversation': {
                        'max_duration_seconds': 300,
                        'client_events': ['audio', 'interruption', 'user_transcript', 'agent_response', 'agent_response_correction', 'client_tool_call'],
                    },
                },
                'platform_settings': {'auth': {'enable_auth': True}},
            }
            state['agent_id'] = post('/v1/convai/agents/create', config)['agent_id']
            remember()
    set_key(str(env), 'ELEVENLABS_AGENT_ID', state['agent_id'])
    env.chmod(0o600)
    print('VoiceTasks agent configured:', state['agent_id'])


if __name__ == '__main__':
    main()
