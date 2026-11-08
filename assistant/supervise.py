"""Hidden project-only crash recovery. STOP is authoritative, never auto-resends."""
import msvcrt
import subprocess
import sys
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parent

def acquire(lock,stop,timeout=5):
    """Wait briefly for an exiting supervisor; never run two workers."""
    deadline=time.monotonic()+timeout
    while not stop.exists():
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
            return True
        except OSError:
            if time.monotonic()>=deadline:return False
            time.sleep(.05)
    return False

def main():
    stop=ROOT/'runtime/STOP'
    with open(ROOT/'runtime/supervisor.lock','a+b') as lock:
        if not acquire(lock,stop):return
        delay=3
        while not stop.exists():
            started=time.monotonic()
            child=subprocess.Popen([sys.executable,str(ROOT/'run_bot.py')],cwd=ROOT,
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,stdin=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW)
            child.wait()
            if stop.exists(): break
            delay=3 if time.monotonic()-started>60 else min(60,delay*2)
            until=time.monotonic()+delay
            while time.monotonic()<until and not stop.exists(): time.sleep(.5)

if __name__=='__main__':main()
