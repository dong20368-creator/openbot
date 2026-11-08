"""Synthetic recovery faults; no model, network, or real messaging."""
import hashlib,queue,tempfile,time,unittest
from pathlib import Path
from core import Store,Message,Worker
from codex_client import Codex

class RecoveryAudit(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.tmp.name)/'state.db','bot',{'room'},0)
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def seed(self,count):
        for i in range(count):
            self.store.ingest(Message('bot','room',str(i),f'member{i}',('bot',),'synthetic',time.time()))
        self.store.db.execute("UPDATE jobs SET state='uncertain',answer='synthetic',answer_hash=?",(hashlib.sha256(b'synthetic').hexdigest(),))
    def receipt(self,job):
        return {'account':'bot','group':'room','message_id':f"reply-{job['id']}",'reference_id':job['message_id'],'text_sha256':job['answer_hash']}
    def test_old_uncertain_is_not_starved_by_five_newer_unknowns(self):
        self.seed(7);owner=self;seen=[]
        class Sender:
            def find_receipt(self,*args):raise AssertionError('use original intent only')
            def find_job_receipt(self,job):
                seen.append(job['id']);return owner.receipt(job) if job['id']==1 else None
        w=Worker(self.store,None,Sender(),'send')
        for _ in range(3):w.reconcile()
        self.assertIn(1,seen)
        self.assertEqual(self.store.db.execute('SELECT state FROM jobs WHERE id=1').fetchone()[0],'confirmed')
    def test_one_receipt_failure_does_not_block_other_receipts(self):
        self.seed(2);owner=self
        class Sender:
            def find_receipt(self,*args):raise AssertionError('use original intent only')
            def find_job_receipt(self,job):
                if job['id']==1:raise TimeoutError('synthetic private details')
                return owner.receipt(job)
        Worker(self.store,None,Sender(),'send').reconcile()
        self.assertEqual(self.store.status(),{'confirmed':1,'uncertain':1})
        details=' '.join(r[0] for r in self.store.db.execute('SELECT metadata FROM job_events'))
        self.assertNotIn('private details',details)
    def test_round_robin_checks_all_ids_in_bounded_batches(self):
        self.seed(12);seen=[]
        class Sender:
            def find_job_receipt(self,job):seen.append(job['id']);return None
        worker=Worker(self.store,None,Sender(),'send')
        for _ in range(3):
            before=len(seen);worker.reconcile();self.assertLessEqual(len(seen)-before,5)
        self.assertEqual(set(seen),set(range(1,13)))
        self.assertEqual(self.store.status(),{'uncertain':12})
    def test_malformed_and_wrong_scope_receipts_never_confirm(self):
        self.seed(2)
        class Sender:
            def find_job_receipt(self,job):
                return ['invalid'] if job['id']==1 else {'account':'other','group':'room'}
        Worker(self.store,None,Sender(),'send').reconcile()
        self.assertEqual(self.store.status(),{'uncertain':2})
    def test_silent_model_timeout_has_stable_failure_code(self):
        model=Codex();model.events=queue.Queue();model.deadline=time.monotonic()+.01
        with self.assertRaisesRegex(TimeoutError,'codex_timeout'):model.receive()

if __name__=='__main__':unittest.main()
