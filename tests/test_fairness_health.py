import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from core import Store,Message,Worker
from group20_eval import FakeSender
from runtime_health import snapshot,monitor_loop
from evaluation_run import create_run

class FairnessHealth(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).parent/'runtime')
        self.path=Path(self.tmp.name)/'db.sqlite3';self.now=time.time()
        self.store=self.open()
    def open(self):return Store(self.path,'synthetic-bot',{'synthetic-room'},self.now-100,max_queue_age=1800)
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def add(self,ident,person,created=None):
        self.assertTrue(self.store.ingest(Message('synthetic-bot','synthetic-room',ident,person,('synthetic-bot',),ident,self.now-10 if created is None else created)))
    def test_twenty_members_one_flooder_first_round_fair_and_no_drops(self):
        for i in range(20):self.add(f'flood-{i}','flood',self.now-20)
        for i in range(19):self.add(f'normal-{i}',f'p{i}')
        class Model:
            def answer(self,q,h):return 'synthetic '+q
        sender=FakeSender()
        generator=Worker(self.store,Model(),FakeSender(),'send',stage='generate')
        delivery=Worker(self.store,None,sender,'send',stage='deliver')
        for _ in range(39):
            self.assertTrue(generator.run_one());self.assertTrue(delivery.run_one())
        first_round=[r['member'] for r in sender.sent[:20]]
        self.assertEqual(len(set(first_round)),20)
        self.assertEqual([r['source'] for r in sender.sent if r['member']=='flood'],[f'flood-{i}' for i in range(20)])
        self.assertEqual(self.store.status(),{'confirmed':39})
    def test_fairness_persists_restart_and_forget_removes_turn(self):
        self.add('a1','a',self.now-20);self.add('a2','a',self.now-20);self.add('b1','b')
        first=self.store.claim(stage='generate')
        self.store.db.execute("UPDATE jobs SET state='confirmed',answer='synthetic' WHERE id=?",(first['id'],))
        self.store.close();self.store=self.open()
        self.assertEqual(self.store.claim(stage='generate')['question'],'b1')
        self.store.forget('synthetic-room','a')
        self.assertIsNone(self.store.db.execute("SELECT 1 FROM generation_turns WHERE sender='a'").fetchone())
    def test_health_uses_completion_time_not_job_id_and_has_no_content(self):
        self.add('private synthetic question','secret member');self.add('second','other')
        self.store.audit(1,{'category':'conversation'});self.store.audit(2,{'category':'verified'})
        self.store.db.execute('UPDATE model_audits SET created=? WHERE job_id=1',(self.now-2,))
        self.store.db.execute('UPDATE model_audits SET created=? WHERE job_id=2',(self.now-10,))
        detail=snapshot(self.store,now=self.now)
        self.assertEqual(detail['last_model_job_id'],1)
        self.assertEqual(detail['last_model_age_seconds'],2)
        self.assertEqual(detail['oldest_wait_seconds'],10)
        text=json.dumps(detail)
        self.assertNotIn('private synthetic',text);self.assertNotIn('secret member',text)
    def test_monitor_recovers_transient_error_and_error_report_failure(self):
        stop=threading.Event();calls=[]
        def publish():
            calls.append('publish')
            if len(calls)<3:raise OSError('synthetic')
            stop.set()
        def error():calls.append('error');raise OSError('synthetic status write')
        monitor_loop(stop,publish,error,interval=.001)
        self.assertEqual(calls,['publish','error','publish'])

    def test_evaluation_runs_cannot_overwrite_and_manifest_excludes_prompt(self):
        a=create_run('test',[('case','synthetic private prompt')],base=self.tmp.name)
        b=create_run('test',[('case','synthetic private prompt')],base=self.tmp.name)
        self.assertNotEqual(a,b)
        manifest=json.loads((a/'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(len(manifest['source_sha256']['core.py']),64)
        self.assertNotIn('synthetic private prompt',json.dumps(manifest))
        self.assertFalse(manifest['real_group_send'])

if __name__=='__main__':unittest.main()
