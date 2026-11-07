"""Inspect tracked release files and commit identity without printing secrets."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_PARTS = {'runtime', 'audit', 'data', '__pycache__', '.venv', 'node_modules', 'target'}
SECRET = re.compile(r'gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{25,}|sk-[A-Za-z0-9_-]{25,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')


def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--require-anonymous-root', action='store_true')
    args = parser.parse_args()
    names = [s for s in git('ls-files', '-z').decode().split('\0') if s]
    if not names:
        raise SystemExit('No tracked files: stage the reviewed release first.')
    errors = []
    for name in names:
        path = Path(name)
        if FORBIDDEN_PARTS.intersection(path.parts) or path.name in {'auth.json', '.env', 'config.local.json'}:
            errors.append({'file': name, 'reason': 'private_runtime_path'})
        raw = (ROOT / name).read_bytes()
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError:
            continue
        if SECRET.search(text):
            errors.append({'file': name, 'reason': 'credential_pattern'})
        if path.suffix == '.py':
            try:
                ast.parse(text, filename=name)
            except SyntaxError:
                errors.append({'file': name, 'reason': 'python_syntax'})
    if args.require_anonymous_root:
        commits = git('rev-list', '--all').decode().splitlines()
        if len(commits) != 1:
            errors.append({'reason': 'release_must_have_one_root_commit'})
        for sha in commits:
            fields = git('show', '-s', '--format=%P%n%ae%n%ce', sha).decode().splitlines()
            if fields[0] or any(not email.endswith('@openbot.invalid') for email in fields[1:]):
                errors.append({'reason': 'nonanonymous_commit_metadata'})
    print(json.dumps({'tracked_files': len(names), 'passed': not errors, 'findings': errors}, indent=2))
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
