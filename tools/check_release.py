"""Inspect tracked release files and commit identity without printing secrets."""
import argparse
import ast
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_PARTS = {'runtime', 'audit', 'data', '__pycache__', '.venv', 'node_modules', 'target'}
SECRET = re.compile(r'gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{25,}|sk-[A-Za-z0-9_-]{25,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')


def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args])


def check_history(single_root=False):
    errors=[]
    commits=git('rev-list','--all').decode().splitlines()
    if single_root and len(commits)!=1:errors.append({'reason':'release_must_have_one_root_commit'})
    for sha in commits:
        fields=git('show','-s','--format=%P%n%an%n%ae%n%cn%n%ce',sha).decode().splitlines()
        if (len(fields)!=5 or (single_root and fields[0])
            or fields[1]!='OpenBot contributors' or fields[3]!='OpenBot contributors'
            or fields[2]!='contributors@openbot.invalid' or fields[4]!='contributors@openbot.invalid'):
            errors.append({'reason':'nonanonymous_commit_metadata','commit':sha})
    return errors


def check_manifest():
    """Compare the staged manifest to staged blobs, never to stale disk hashes."""
    try:
        manifest=json.loads(git('show',':MANIFEST.json'))
        expected={}
        for record in git('ls-files','--stage','-z').decode().split('\0'):
            if not record:continue
            meta,name=record.split('\t',1);mode,sha,stage=meta.split()
            if stage!='0':return [{'reason':'unmerged_index'}]
            if name!='MANIFEST.json':expected[name]=(sha,mode)
        rows=manifest['files']
        actual={row['path']:(row['git_blob_sha'],row['mode']) for row in rows}
        if len(actual)!=len(rows) or actual!=expected:return [{'reason':'manifest_index_mismatch'}]
        return []
    except (KeyError,TypeError,ValueError,subprocess.CalledProcessError):
        return [{'reason':'invalid_staged_manifest'}]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--require-anonymous-root', action='store_true')
    parser.add_argument('--check-anonymous-history', action='store_true',help='Check every reachable commit without requiring a single root-only release.')
    parser.add_argument('--check-manifest', action='store_true',help='Verify staged manifest paths, modes and Git blob IDs.')
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
    if args.require_anonymous_root or args.check_anonymous_history:
        errors.extend(check_history(args.require_anonymous_root))
    if args.check_manifest:errors.extend(check_manifest())
    print(json.dumps({'tracked_files': len(names), 'passed': not errors, 'findings': errors}, indent=2))
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
