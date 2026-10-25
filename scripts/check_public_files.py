"""Check staged Git blobs, including files force-added past .gitignore.

This supplements Gitleaks; it is not a general-purpose secret detector.
Never print matching credential values.
"""
import re
import subprocess
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_DIRS = {'.local', '.venv', 'node_modules', '__pycache__', '.pytest_cache'}
PRIVATE_SUFFIXES = {'.wav', '.aiff', '.mp3', '.mp4', '.pem', '.key', '.p12', '.pfx', '.db', '.log'}
TOKEN = re.compile(rb'\b(?:sk_[a-fA-F0-9]{32,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b')
PRIVATE_KEY = re.compile(rb'-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----')


def violations(name: str, data: bytes) -> list[str]:
    path = PurePosixPath(name)
    reasons = []
    if any(part in PRIVATE_DIRS for part in path.parts) or name.startswith('web/dist/'):
        reasons.append('local/generated directory')
    if path.suffix.lower() in PRIVATE_SUFFIXES or '.sqlite3' in path.name:
        reasons.append('local data, recording, log, or credential file')
    if path.name.startswith('.env') and path.name != '.env.example':
        reasons.append('environment file')
    if path.name == '.env.example':
        for line in data.decode('utf-8').splitlines():
            if line.strip() and not line.lstrip().startswith('#'):
                key, separator, value = line.partition('=')
                if not separator or value.strip() or key not in {'ELEVENLABS_API_KEY', 'ELEVENLABS_AGENT_ID'}:
                    reasons.append('environment example must contain only empty configuration placeholders')
                    break
    if TOKEN.search(data) or PRIVATE_KEY.search(data):
        reasons.append('possible hardcoded credential')
    return reasons


def main():
    entries = subprocess.check_output(['git', 'ls-files', '--stage', '-z'], cwd=ROOT).split(b'\0')
    failures = []
    count = 0
    for entry in filter(None, entries):
        metadata, path = entry.split(b'\t', 1)
        mode, oid, stage = metadata.split()
        name = path.decode('utf-8')
        count += 1
        if mode not in {b'100644', b'100755'} or stage != b'0':
            failures.append((name, ['unresolved merge, symlink, or submodule; review before publishing']))
            continue
        data = subprocess.check_output(['git', 'cat-file', 'blob', oid.decode()], cwd=ROOT)
        reasons = violations(name, data)
        if reasons:
            failures.append((name, reasons))
    if not count:
        raise SystemExit('No staged/tracked files found. Stage the release files before checking.')
    if failures:
        for name, reasons in failures:
            print(f'BLOCKED {name}: {"; ".join(reasons)}')
        raise SystemExit(1)
    print(f'Public-file guard passed for {count} staged/tracked files.')


if __name__ == '__main__':
    main()
