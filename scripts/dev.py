"""Run the local API and frontend together; Ctrl+C stops both."""
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    node = shutil.which('node')
    if not node or not (ROOT / 'web' / 'node_modules').is_dir():
        raise SystemExit('Install Node.js and run npm ci in web/ first.')
    for port in (8001, 5174):
        with socket.socket() as check:
            check.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                check.bind(('127.0.0.1', port))
            except OSError:
                raise SystemExit(f'Port {port} is already in use. Stop the previous server or open http://127.0.0.1:5174 if VoiceTasks is already running.')
    processes = []
    try:
        processes.append(subprocess.Popen([sys.executable, '-m', 'uvicorn', 'backend.app:app', '--host', '127.0.0.1', '--port', '8001'], cwd=ROOT))
        processes.append(subprocess.Popen([node, str(ROOT / 'web/node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', '5174', '--strictPort'], cwd=ROOT / 'web'))
        print('\nVoiceTasks: http://127.0.0.1:5174 — keep this terminal open; Ctrl+C stops both servers.\n', flush=True)
        while all(process.poll() is None for process in processes):
            time.sleep(.25)
        raise SystemExit('A development server exited. Check the output above.')
    except KeyboardInterrupt:
        pass
    finally:
        for process in processes:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)


if __name__ == '__main__':
    main()
