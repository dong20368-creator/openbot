"""Burst faults across 20 synthetic members, using production Store/Worker."""
import tempfile
import json
import time
from pathlib import Path
import unittest
from core import Store,Message,Worker
from group20_eval import FakeSender

class BurstTests(unittest.TestCase):
    def test_diagnostics_retain_read_counts_and_repairs_without_raw_payload(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent/'runtime') as tmp:
            store=Store(Path(tmp)/'queue.sqlite3','synthetic-bot',{'synthetic-room'},time.time()-1)
            try:
                store.audit(1,{'category':'verified','evidence_passed':False,'futu_reads':2,
                    'math_calls':4,'evidence_grounding_attempted':True,
                    'raw_payload':'private synthetic payload','token':'synthetic secret'})
                audit=json.loads(store.db.execute('SELECT metadata FROM model_audits WHERE job_id=1').fetchone()[0])
                self.assertEqual(audit['futu_reads'],2)
                self.assertEqual(audit['math_calls'],4)
                self.assertTrue(audit['evidence_grounding_attempted'])
                self.assertFalse(audit['evidence_passed'])
                self.assertNotIn('raw_payload',audit);self.assertNotIn('token',audit)
            finally:store.close()

    def test_twenty_members_duplicate_retry_revocation_and_followup(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent/'runtime') as tmp:
            now=time.time()
            store=Store(Path(tmp)/'queue.sqlite3','synthetic-bot',{'synthetic-room'},now-1,max_queue_age=1800)
            try:
                for i in range(20):
                    msg=Message('synthetic-bot','synthetic-room',f'm{i}',f'p{i}',('synthetic-bot',),f'我的测试预算是{i+1}万元',now)
                    self.assertTrue(store.ingest(msg));self.assertFalse(store.ingest(msg))
                class Model:
                    attempts=0
                    def answer(self,q,h):
                        if q=='我的测试预算是1万元':
                            self.attempts+=1
                            if self.attempts==1:raise TimeoutError('synthetic')
                        if q=='我刚才预算多少？':
                            own=[x for x in h if x['kind']=='current_person_history']
                            if len(own)!=1 or own[0]['question']!='我的测试预算是1万元':raise AssertionError('mixed history')
                            return '1万元'
                        if h:raise AssertionError('cross-member history')
                        return '收到：'+q
                class Sender(FakeSender):
                    def source_info(self,job):return {'source_state':'revoked' if job['message_id']=='m7' else 'present'}
                sender=Sender();worker=Worker(store,Model(),sender,'send')
                self.assertTrue(worker.run_one()) # first member retries later
                self.assertTrue(worker.run_one()) # second member proceeds
                self.assertEqual(sender.sent[0]['member'],'p1')
                for _ in range(25):
                    store.db.execute('UPDATE jobs SET next_attempt=0')
                    if not worker.run_one():break
                self.assertEqual(store.status(),{'cancelled':1,'confirmed':19})
                self.assertEqual(len(sender.sent),19)
                self.assertNotIn('p7',[x['member'] for x in sender.sent])
                self.assertTrue(store.ingest(Message('synthetic-bot','synthetic-room','follow','p0',('synthetic-bot',),'我刚才预算多少？',time.time())))
                self.assertTrue(worker.run_one())
                self.assertEqual(sender.sent[-1]['answer'],'1万元')
                self.assertEqual(len(sender.sent),20)
            finally:store.close()

if __name__=='__main__':unittest.main()
