"""Prepare a real group reply without sending; UI delivery remains separate.

Uses the same durable queue and context store as the worker. Never marks a
prepared response confirmed until a real WeChat receipt is checked separately.
"""
import json
import os
from pathlib import Path
import subprocess
import time
from core import Message,Store
from codex_client import Codex

ROOT=Path(__file__).resolve().parent


def export_inbox():
    run=subprocess.run(['wsl','-d','WeChatAgent','-u','root','--exec','python3',
        '/opt/wechat-agent-v2/inbox.py','--export'],capture_output=True,encoding='utf-8',timeout=45,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    if run.returncode: raise RuntimeError('inbox_unavailable')
    return json.loads(run.stdout)


def prepare():
    data=export_inbox(); config=data['config']
    settings=ROOT/'runtime/activation.json'
    if not settings.exists():
        settings.write_text(json.dumps({'activated_at':time.time()-600}),encoding='utf-8')
    active=json.loads(settings.read_text(encoding='utf-8'))['activated_at']
    store=Store(ROOT/'runtime/state.sqlite3',config['account'],{config['group']},active)
    try:
        for msg in data['messages']: store.ingest(Message(**msg))
        job=store.claim()
        if not job: return {'prepared':False,'states':store.status()}
        try:
            model=Codex(timeout=150)
            answer=model.answer(job['question'],store.history(job,True))
            if not answer or len(answer)>4000: raise ValueError('Invalid answer')
            store.propose_facts(job,model.last_memories)
            store.transition(job['id'],'generating','ready',answer=answer)
            return {'prepared':True,'job':job['id'],'answer':answer,'audit':model.last_audit}
        except Exception:
            store.transition(job['id'],'generating','failed',error_code='model_failed')
            raise
    finally: store.close()


if __name__=='__main__':
    # This process owns a Windows byte-range lock throughout prepare/claim.
    import msvcrt
    with open(ROOT/'runtime/worker.lock','a+b') as lock:
        lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
        msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        try: print(json.dumps(prepare()))
        finally:
            lock.seek(0); msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
