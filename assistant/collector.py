"""Independent durable intake: a slow model never blocks new @ ingestion."""
import json
import os
import subprocess
import threading
import time
import queue
from core import Store,Message


class Collector:
    def __init__(self,path,config,activated_at):
        self.path,self.config,self.activated_at=path,dict(config),activated_at
        self.stop=threading.Event(); self.changed=threading.Event(); self.lock=threading.Lock(); self.proc=None
        self.state={'healthy':False,'last_seen':0,'accepted':0,'decryptions':0,'restarts':0}
        self.on_jobs=lambda:None

    def snapshot(self):
        with self.lock: return dict(self.state),dict(self.config)

    def update(self,**data):
        with self.lock: self.state.update(data)

    def accept(self,store,data):
        kind=data.get('kind')
        if kind=='error':
            reason=data.get('error_code')
            if reason not in ('native_auth_required','native_reconnecting','native_transport_unavailable','native_target_changed'):reason='snapshot_unavailable'
            previous,_=self.snapshot()
            if previous.get('reason')!=reason:
                store.event(None,'connection_unhealthy',reason=reason)
                self.update(last_disconnect_at=time.time(),last_disconnect_reason=reason)
            self.update(healthy=False,reason=reason)
            return
        if kind=='batch':
            config=data['config']
            if (config['account'],config['group'])!=(self.config['account'],self.config['group']):
                raise ValueError('Intake scope changed')
            accepted=0
            for msg in data['messages']:
                try: accepted+=store.ingest(Message(**msg))
                except (TypeError,ValueError): continue
            with self.lock:
                self.config=dict(config); self.state['accepted']+=accepted
            if accepted: self.changed.set()
        elif kind!='heartbeat': raise ValueError('Unknown intake event')
        previous,_=self.snapshot()
        if previous.get('reason'):
            store.event(None,'connection_recovered',previous_reason=previous['reason'])
            self.update(last_recovered_at=time.time())
        self.update(healthy=True,reason=None,last_seen=time.time(),decryptions=data.get('decryptions',0))
        if kind=='batch' and accepted:self.on_jobs()

    def run(self):
        store=Store(self.path,self.config['account'],{self.config['group']},self.activated_at,max_queue_age=1800)
        try:
            while not self.stop.is_set():
                try:
                    self.proc=subprocess.Popen(['wsl','-d','WeChatAgent','-u','root','--exec','python3','-u',
                        '/opt/wechat-agent-v2/inbox.py','--watch'],stdout=subprocess.PIPE,stdin=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,encoding='utf-8',
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                    self.consume(store,self.proc)
                    if not self.stop.is_set(): raise RuntimeError('Intake disconnected')
                except Exception:
                    self.update(healthy=False,reason='intake_disconnected')
                finally:
                    if self.proc and self.proc.poll() is None:
                        self.proc.terminate()
                        try: self.proc.wait(timeout=5)
                        except subprocess.TimeoutExpired: self.proc.kill()
                    self.proc=None
                if not self.stop.is_set():
                    with self.lock: self.state['restarts']+=1
                    self.stop.wait(3)
        finally: store.close()

    def start(self):
        self.thread=threading.Thread(target=self.run,name='wechat-intake',daemon=True)
        self.thread.start()

    def consume(self,store,proc,timeout=45):
        """A blocked stdout read cannot prevent heartbeat expiry or stop."""
        events=queue.Queue()
        def reader():
            try:
                for line in proc.stdout: events.put(line)
            finally: events.put(None)
        thread=threading.Thread(target=reader,name='wechat-intake-pipe',daemon=True); thread.start()
        last=time.monotonic()
        try:
            while not self.stop.is_set():
                try: line=events.get(timeout=min(1,max(.01,timeout-(time.monotonic()-last))))
                except queue.Empty:
                    if time.monotonic()-last>=timeout: raise TimeoutError('intake_heartbeat_expired')
                    continue
                if line is None: return
                self.accept(store,json.loads(line));last=time.monotonic()
        finally:
            if proc.poll() is None:
                proc.terminate()
                try: proc.wait(timeout=5)
                except subprocess.TimeoutExpired: proc.kill();proc.wait(timeout=5)
            thread.join(timeout=2)

    def close(self):
        self.stop.set()
        proc=self.proc
        if proc and proc.poll() is None: proc.terminate()
        self.thread.join(timeout=8)
