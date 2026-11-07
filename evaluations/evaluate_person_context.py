"""Synthetic per-person live checks; isolated SQLite, never send to WeChat."""
import json
import tempfile
import time
from pathlib import Path
from core import Store,Message,Worker
from codex_client import Codex
from test_core import Sender,Model

def main():
    with tempfile.TemporaryDirectory(dir='runtime') as tmp:
        path=Path(tmp)/'test.sqlite3';now=time.time()
        store=Store(path,'synthetic_bot',{'synthetic_room'},now-100)
        statements=[('alice','这次预算3500元，选蓝色'),('bob','这次预算800元，选红色'),
                    ('carol','这次预算1200元，选绿色'),('alice','纠正一下，我这次预算改为4500元，颜色不变')]
        for i,(person,q) in enumerate(statements):
            store.ingest(Message('synthetic_bot','synthetic_room',str(i),person,('synthetic_bot',),q,now+i),now=now+i)
            Worker(store,Model(),Sender(),'send').run_one()
        store.close()
        # Reopen to verify continuity is stored on disk, not an in-memory chat.
        store=Store(path,'synthetic_bot',{'synthetic_room'},now-100)
        try:
            for i,(person,amount,color) in enumerate([('alice','4500','蓝'),('bob','800','红'),('carol','1200','绿')],10):
                store.ingest(Message('synthetic_bot','synthetic_room',str(i),person,('synthetic_bot',),'我刚才的预算和颜色分别是什么？',now+i),now=now+i)
                job=store.claim();history=store.history(job,True)
                c=Codex(timeout=60);answer=c.answer(job['question'],history)
                passed=amount in answer and color in answer and all(x not in answer for x in {'4500','800','1200'}-{amount})
                print(json.dumps(dict(person=person,passed=passed,own_turns=len([h for h in history if h['kind']=='current_person_history']),answer=answer),ensure_ascii=True),flush=True)
                if not passed:raise AssertionError('person_context_mismatch')
                store.transition(job['id'],'generating','preview')
        finally:store.close()

if __name__=='__main__':main()
