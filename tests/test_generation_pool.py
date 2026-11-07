import hashlib
from pathlib import Path
import tempfile
import threading
import time
import unittest
from core import Store,Message,Worker
from generation_pool import GenerationPool
from group20_eval import FakeSender

class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).parent/'runtime')
        self.path=Path(self.tmp.name)/'db.sqlite3';self.now=time.time()
        self.store=self.factory();self.pool=None
    def tearDown(self):
        if self.pool:self.pool.close()
        self.store.close();self.tmp.cleanup()
    def factory(self):return Store(self.path,'synthetic-bot',{'synthetic-room'},self.now-1,max_queue_age=1800)
    def add(self,ident,person):
        self.assertTrue(self.store.ingest(Message('synthetic-bot','synthetic-room',ident,person,('synthetic-bot',),ident,time.time())))
    def until(self,predicate,seconds=5):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            if predicate():return
            time.sleep(.01)
        self.fail('condition did not become true')

    def test_slow_member_does_not_block_other_but_own_followup_waits_for_receipt(self):
        self.add('slow','a');self.add('follow','a');self.add('fast','b')
        release=threading.Event();begun=threading.Event();observed=[]
        class Model:
            def answer(inner,q,h):
                observed.append((q,h))
                if q=='slow':begun.set();release.wait(4)
                return 'answer '+q
        self.pool=GenerationPool(self.factory,Model,FakeSender,poll=.01);self.pool.start()
        try:
            self.assertTrue(begun.wait(2))
            self.until(lambda:self.store.db.execute("SELECT state FROM jobs WHERE question='fast'").fetchone()[0]=='ready')
            self.assertEqual(self.store.db.execute("SELECT state FROM jobs WHERE question='follow'").fetchone()[0],'pending')
            sender=FakeSender();delivery=Worker(self.store,None,sender,'send',stage='deliver')
            self.assertTrue(delivery.run_one());self.assertEqual(sender.sent[0]['source'],'fast')
            release.set()
            self.until(lambda:self.store.db.execute("SELECT state FROM jobs WHERE question='slow'").fetchone()[0]=='ready')
            self.assertNotIn('follow',[q for q,h in observed])
            self.assertTrue(delivery.run_one())
            self.until(lambda:self.store.db.execute("SELECT state FROM jobs WHERE question='follow'").fetchone()[0]=='ready')
            history=[h for q,h in observed if q=='follow'][0]
            self.assertEqual([h['question'] for h in history if h.get('kind')=='current_person_history'],['slow'])
            self.assertTrue(delivery.run_one());self.assertEqual(self.store.status(),{'confirmed':3})
        finally:release.set()

    def test_stop_persists_ready_without_sending_and_recover_rechecks(self):
        self.add('one','a')
        class Model:
            def answer(self,*a):return 'answer'
        self.pool=GenerationPool(self.factory,Model,FakeSender,poll=.01);self.pool.start()
        self.until(lambda:self.store.status()=={'ready':1})
        self.pool.close();self.store.recover()
        sender=FakeSender();Worker(self.store,None,sender,'send',stage='deliver').run_one()
        self.assertEqual(len(sender.sent),1)

    def test_same_member_waits_until_confirmed_facts_are_committed(self):
        self.add('one','a');self.add('two','a');self.add('other','b')
        first=self.store.claim(stage='generate')
        self.store.db.execute("INSERT INTO proposed_facts VALUES(?,?,?,?)",(first['id'],'budget','synthetic','one'))
        self.store.db.execute("UPDATE jobs SET state='confirmed',answer='answer' WHERE id=?",(first['id'],))
        next_job=self.store.claim(stage='generate')
        self.assertEqual(next_job['question'],'other')
        self.assertIsNone(self.store.claim(stage='generate'))
        sender=FakeSender()
        Worker(self.store,None,sender,'send',stage='deliver').reconcile()
        self.assertEqual(sender.sent,[])
        self.assertEqual(self.store.claim(stage='generate')['question'],'two')

    def test_unknown_delivery_blocks_all_ready_answers(self):
        for i in range(2):self.add(str(i),str(i))
        class Model:
            def answer(self,*a):return 'answer'
        class Uncertain(FakeSender):
            def send_and_verify(self,*a):raise TimeoutError('synthetic lost send response')
        generator=Worker(self.store,Model(),FakeSender(),'send',stage='generate')
        self.assertTrue(generator.run_one());self.assertTrue(generator.run_one())
        delivery=Worker(self.store,None,Uncertain(),'send',stage='deliver')
        self.assertTrue(delivery.run_one());self.assertFalse(delivery.run_one())
        self.assertEqual(self.store.status(),{'ready':1,'uncertain':1})

    def test_stale_ready_answer_regenerates_without_dropping_same_person_order(self):
        self.add('one','a');self.add('two','a')
        class Model:
            last_audit={'category':'verified','evidence_passed':True}
            def answer(self,*a):return 'answer'
        generation=Worker(self.store,Model(),FakeSender(),'send',stage='generate')
        self.assertTrue(generation.run_one());self.assertFalse(generation.run_one())
        self.store.db.execute('UPDATE model_audits SET created=?',(time.time()-121,))
        sender=FakeSender();delivery=Worker(self.store,None,sender,'send',stage='deliver')
        self.assertTrue(delivery.run_one());self.assertEqual(sender.sent,[])
        self.assertEqual(self.store.status(),{'pending':2})
        self.assertTrue(generation.run_one());self.assertTrue(delivery.run_one())
        self.assertEqual(sender.sent[0]['source'],'one')

    def test_only_three_models_execute_and_generation_never_sends(self):
        for i in range(20):self.add(str(i),str(i))
        counts={'active':0,'max':0,'entered':0};lock=threading.Lock();first_three=threading.Barrier(3,timeout=3)
        class Model:
            def answer(self,*a):
                with lock:
                    counts['active']+=1;counts['max']=max(counts['max'],counts['active'])
                    counts['entered']+=1;initial=counts['entered']<=3
                if initial:first_three.wait()
                with lock:counts['active']-=1
                return 'answer'
        class NoSend(FakeSender):
            def send_and_verify(self,*a):raise AssertionError('generation tried to send')
        self.pool=GenerationPool(self.factory,Model,NoSend,poll=.01);self.pool.start()
        self.until(lambda:self.store.status()=={'ready':20})
        self.assertEqual(counts['max'],3)
        sender=FakeSender();delivery=Worker(self.store,None,sender,'send',stage='deliver')
        while delivery.run_one():pass
        self.assertEqual(len(sender.sent),20);self.assertEqual(self.store.status(),{'confirmed':20})

    def test_each_worker_warms_and_closes_its_own_model(self):
        models=[];lock=threading.Lock()
        class Model:
            def __init__(self):
                self.warmed=threading.Event();self.closed=False
                with lock:models.append(self)
            def warmup(self):self.warmed.set()
            def close(self):self.closed=True
        self.pool=GenerationPool(self.factory,Model,FakeSender,poll=.01);self.pool.start()
        self.until(lambda:len(models)==3 and all(m.warmed.is_set() for m in models))
        self.pool.close()
        self.assertTrue(all(m.closed for m in models))

    def test_arrival_wakes_sleeping_worker_without_poll_delay(self):
        idle=threading.Event();answered=threading.Event()
        class Model:
            def answer(self,*args):answered.set();return 'answer'
        def health():idle.set();return True
        self.pool=GenerationPool(self.factory,Model,FakeSender,healthy=health,workers=1,poll=30)
        self.pool.start();self.assertTrue(idle.wait(2))
        self.add('arriving','member');self.pool.wake()
        self.assertTrue(answered.wait(2))

if __name__=='__main__':unittest.main()
