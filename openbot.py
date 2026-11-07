"""Developer-friendly, non-sending entry point. Live startup remains explicit."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent
APP = ROOT / 'assistant'


def initialize():
    runtime = APP / 'runtime'
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / 'codex-home').mkdir(exist_ok=True)
    templates = {
        'activation.json': {'activated_at': time.time()},
        'media-consent.json': {'openai_explicit_references': False},
    }
    for name, value in templates.items():
        path = runtime / name
        try:
            with path.open('x', encoding='utf-8') as handle:
                json.dump(value, handle, indent=2)
            print('Created:', name)
        except FileExistsError:
            print('Kept existing:', name)
    print('No login, backend startup, group send or media permission was performed.')


def check():
    runtime = APP / 'runtime'
    binary = os.environ.get('WECHAT_CODEX_BINARY') or shutil.which('codex')
    checks = {
        'windows_host': os.name == 'nt',
        'python_3_12_or_later': sys.version_info >= (3, 12),
        'runtime_initialized': (runtime / 'activation.json').is_file(),
        'media_consent_config_present': (runtime / 'media-consent.json').is_file(),
        'codex_binary_discoverable': bool(binary and (shutil.which(binary) or Path(binary).is_file())),
        'dedicated_login_file_present': (runtime / 'codex-home/auth.json').is_file(),
    }
    print(json.dumps(checks, indent=2))
    print('Presence checks only. WSL, target identity, model access and message delivery are not tested.')
    return 0 if all(checks.values()) else 1


def demo():
    sys.path.insert(0, str(APP))
    from core import Message, Store, Worker

    class FakeModel:
        def answer(self, question, history):
            return 'This is a synthetic reply. No model or network was used.'

    class FakeSender:
        def verify_target(self, account, room):
            return (account, room) == ('demo-bot', 'demo-room')

        def send_and_verify(self, account, room, text):
            return {'account': account, 'group': room, 'message_id': 'synthetic-demo-receipt',
                    'text_sha256': hashlib.sha256(text.encode()).hexdigest()}

    # Test files never share the application's real runtime directory.
    scratch = ROOT / 'tests/runtime'
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='openbot-demo-', dir=scratch) as folder:
        now = time.time()
        store = Store(Path(folder) / 'demo.sqlite3', 'demo-bot', {'demo-room'}, now - 60)
        try:
            message = Message('demo-bot', 'demo-room', 'demo-message', 'demo-member',
                              ('demo-bot',), 'Explain the message pipeline.', now)
            first = store.ingest(message, now=now)
            duplicate = store.ingest(message, now=now)
            Worker(store, FakeModel(), FakeSender(), 'send').run_one()
            result = {'accepted': first, 'duplicate_rejected': not duplicate,
                      'states': store.status(), 'real_messages_sent': 0}
            print(json.dumps(result, indent=2))
            return 0 if first and not duplicate and result['states'].get('confirmed') == 1 else 1
        finally:
            store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('demo', 'test', 'init', 'check'))
    args = parser.parse_args()
    if args.command == 'init':
        initialize()
        return 0
    if args.command == 'check':
        return check()
    if args.command == 'demo':
        return demo()
    return subprocess.call([sys.executable, str(ROOT / 'tools/run_tests.py')])


if __name__ == '__main__':
    raise SystemExit(main())
