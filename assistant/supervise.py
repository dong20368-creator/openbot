"""Hidden project-only crash recovery. STOP is authoritative, never auto-resends."""
import msvcrt
import subprocess
import sys
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parent

with open(ROOT/'runtime/supervisor.lock','a+b') as lock:
    lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
    msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    delay=3
    while not (ROOT/'runtime/STOP').exists():
        started=time.monotonic()
        child=subprocess.Popen([sys.executable,str(ROOT/'run_bot.py')],cwd=ROOT,
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,stdin=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW)
        child.wait()
        if (ROOT/'runtime/STOP').exists(): break
        delay=3 if time.monotonic()-started>60 else min(60,delay*2)
        until=time.monotonic()+delay
        while time.monotonic()<until and not (ROOT/'runtime/STOP').exists(): time.sleep(.5)
