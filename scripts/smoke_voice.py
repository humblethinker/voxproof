"""Live spoken correction check. Uses macOS speech synthesis, never your microphone.

Consumes ElevenLabs credits; no tasks are saved. Run from the project root.
"""
import asyncio
import base64
import contextlib
import json
import subprocess
import wave
from datetime import datetime, timezone
from pathlib import Path

import httpx
import websockets
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / '.local' / 'smoke'


def speech(name, text):
    aiff, wav = OUTPUT / f'{name}.aiff', OUTPUT / f'{name}.wav'
    subprocess.run(['say', '-v', 'Samantha', '-r', '160', '-o', str(aiff), text], check=True)
    subprocess.run(['afconvert', '-f', 'WAVE', '-d', 'LEI16@16000', '-c', '1', str(aiff), str(wav)], check=True)
    with wave.open(str(wav)) as f:
        assert (f.getnchannels(), f.getsampwidth(), f.getframerate()) == (1, 2, 16000)
        return f.readframes(f.getnframes())


async def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    first = speech('task', 'Add buy groceries.')
    correction = speech('correction', 'Actually, buy groceries and coffee.')
    env = dotenv_values(ROOT / '.env')
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get('https://api.elevenlabs.io/v1/convai/conversation/get-signed-url',
            params={'agent_id': env['ELEVENLABS_AGENT_ID']}, headers={'xi-api-key': env['ELEVENLABS_API_KEY']})
        if r.is_error:
            raise RuntimeError(f'Signing failed: HTTP {r.status_code}')
        url = r.json()['signed_url']
    report = {'at': datetime.now(timezone.utc).isoformat(), 'input': 'synthetic spoken PCM, 16 kHz',
              'tool_calls': [], 'transcripts': [], 'responses': [], 'interruptions': 0, 'audio_bytes': 0}
    metadata = asyncio.Event()
    next_audio = asyncio.Event()
    tools = asyncio.Queue()

    async with websockets.connect(url, max_size=4_000_000) as ws:
        async def receive():
            async for raw in ws:
                event = json.loads(raw)
                kind = event.get('type')
                if kind == 'ping':
                    await ws.send(json.dumps({'type': 'pong', 'event_id': event['ping_event']['event_id']}))
                elif kind == 'conversation_initiation_metadata':
                    info = event['conversation_initiation_metadata_event']
                    report['conversation_id'] = info['conversation_id']
                    if info.get('user_input_audio_format') != 'pcm_16000':
                        raise RuntimeError('Agent must use pcm_16000 input.')
                    metadata.set()
                elif kind == 'audio':
                    report['audio_bytes'] += len(base64.b64decode(event['audio_event']['audio_base_64']))
                    next_audio.set()
                elif kind == 'interruption':
                    report['interruptions'] += 1
                elif kind == 'user_transcript':
                    report['transcripts'].append(event['user_transcription_event']['user_transcript'])
                elif kind == 'agent_response':
                    report['responses'].append(event['agent_response_event']['agent_response'])
                elif kind == 'client_tool_call':
                    call = event['client_tool_call']
                    report['tool_calls'].append({'name': call['tool_name'], 'parameters': call['parameters']})
                    valid = call['tool_name'] == 'set_task_draft' and isinstance(call['parameters'].get('title'), str)
                    await ws.send(json.dumps({'type': 'client_tool_result', 'tool_call_id': call['tool_call_id'],
                        'result': 'Draft updated. NOT saved. User must click Confirm.' if valid else 'Invalid tool.', 'is_error': not valid}))
                    next_audio.clear()
                    await tools.put(call)
                elif kind == 'error':
                    raise RuntimeError('ElevenLabs sent an error event.')

        async def send_audio(pcm):
            # 100 ms chunks paced in real time, then silence to finish the turn.
            pcm += b'\0' * 32000
            for offset in range(0, len(pcm), 3200):
                await ws.send(json.dumps({'user_audio_chunk': base64.b64encode(pcm[offset:offset+3200]).decode()}))
                await asyncio.sleep(.1)

        receiver = asyncio.create_task(receive())
        try:
            await ws.send(json.dumps({'type': 'conversation_initiation_client_data'}))
            await asyncio.wait_for(metadata.wait(), 15)
            await asyncio.sleep(3)  # Let the short greeting finish.
            await send_audio(first)
            await asyncio.wait_for(tools.get(), 25)
            await asyncio.wait_for(next_audio.wait(), 15)
            await send_audio(correction)  # Speak over the agent's read-back.
            corrected = await asyncio.wait_for(tools.get(), 25)
            title = corrected['parameters']['title'].lower()
            assert 'groceries' in title and 'coffee' in title, f'Unexpected corrected title: {title}'
            await asyncio.sleep(2)
            assert report['audio_bytes'] > 0
            report['passed'] = True
        finally:
            receiver.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await receiver
            (OUTPUT / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
