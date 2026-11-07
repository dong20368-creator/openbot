"""Log into a dedicated local Codex home; never copy another application's auth."""
from pathlib import Path
import subprocess
import sys
from codex_client import binary, environment


def main():
    runtime = Path(__file__).resolve().parent / 'runtime'
    runtime.mkdir(exist_ok=True)
    return subprocess.call([binary(), 'login'], cwd=runtime, env=environment())


if __name__ == '__main__':
    raise SystemExit(main())
