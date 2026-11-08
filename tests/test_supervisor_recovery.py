"""Actual Windows lock handoff using synthetic processes and temporary files."""
import os,subprocess,sys,tempfile,time,unittest
from pathlib import Path
if os.name=='nt':
    import supervise
    from supervise import acquire

@unittest.skipUnless(os.name=='nt','Windows supervisor locking')
class SupervisorRecovery(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.stop=self.root/'STOP';self.path=self.root/'lock'
        self.children=[]
    def tearDown(self):
        for child in self.children:
            if child.poll() is None:child.terminate()
            child.wait(timeout=3)
            if child.stdout:child.stdout.close()
        self.tmp.cleanup()
    def owner(self,delay,stop=False):
        code="import msvcrt,sys,time;from pathlib import Path; f=open(sys.argv[1],'a+b');msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1);print('locked',flush=True);time.sleep(float(sys.argv[2]));"
        if stop:code+="Path(sys.argv[3]).write_text('pause');time.sleep(.5);"
        child=subprocess.Popen([sys.executable,'-c',code,str(self.path),str(delay),str(self.stop)],stdout=subprocess.PIPE,text=True,creationflags=subprocess.CREATE_NO_WINDOW)
        self.children.append(child);self.assertEqual(child.stdout.readline().strip(),'locked');return child
    def test_resume_waits_for_retiring_owner(self):
        self.owner(.2)
        with self.path.open('a+b') as lock:self.assertTrue(acquire(lock,self.stop,1))
    def test_duplicate_start_does_not_take_active_lock(self):
        self.owner(1)
        with self.path.open('a+b') as lock:self.assertFalse(acquire(lock,self.stop,.1))
    def test_pause_cancels_waiting_resume(self):
        self.owner(.1,True)
        with self.path.open('a+b') as lock:self.assertFalse(acquire(lock,self.stop,1))
    def test_two_supervisors_start_only_one_synthetic_worker(self):
        (self.root/'runtime').mkdir()
        (self.root/'supervise.py').write_text(Path(supervise.__file__).read_text(),encoding='utf-8')
        (self.root/'run_bot.py').write_text("from pathlib import Path\nimport time\np=Path(__file__).parent\nwith (p/'runs').open('a') as f:f.write('one\\n')\ntime.sleep(.2)\n(p/'runtime/STOP').write_text('done')\n",encoding='utf-8')
        for _ in range(2):
            self.children.append(subprocess.Popen([sys.executable,str(self.root/'supervise.py')],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW))
        for child in self.children:
            _,errors=child.communicate(timeout=5);self.assertEqual(child.returncode,0,errors)
        self.assertEqual((self.root/'runs').read_text().splitlines(),['one'])

if __name__=='__main__':unittest.main()
