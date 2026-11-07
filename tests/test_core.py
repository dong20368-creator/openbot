import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import itertools
from core import Message, Store, Worker
from codex_client import checked_reply

class Model:
    def answer(self,question,history):
        return '合成测试回答'

class Sender:
    receipts=itertools.count(1)
    def __init__(self,valid=True,fail=False):
        self.valid,self.fail,self.calls=valid,fail,0
    def verify_target(self,account,room):
        return self.valid
    def send_and_verify(self,account,room,text):
        self.calls+=1
        if self.fail:
            raise TimeoutError()
        return dict(account=account,group=room,message_id='reply'+str(next(self.receipts)),text_sha256=hashlib.sha256(text.encode()).hexdigest())

class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).parent/'runtime')
        self.path=Path(self.tmp.name)/'state.db'
        self.store=Store(self.path,'bot',{'room'},100)
    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()
    def add(self,id='1',**changes):
        data=dict(account='bot',group='room',message_id=id,sender='alice',mentions=('bot',),text='一样的问题',created=101)
        data.update(changes)
        return self.store.ingest(Message(**data),now=102)
    def test_real_mention_and_group_required(self):
        self.assertFalse(self.add(mentions=(),text='@bot 伪造提及'))
        self.assertFalse(self.add(group='other'))
        self.assertFalse(self.add(account='other-account'))
        self.assertFalse(self.add(sender='bot'))
        self.assertFalse(self.add(created=99))
        self.assertTrue(self.add())
    def test_identical_text_different_id_both_accepted(self):
        self.assertTrue(self.add())
        self.assertFalse(self.add())
        self.assertTrue(self.add('2'))
    def test_verified_ordinary_reference_and_chain_scope(self):
        source=dict(account='bot',group='room',message_id='s:77',sender='bob',text='普通群消息',created=100)
        bad=dict(source,chain=[dict(source,group='other',message_id='s:78')])
        self.assertFalse(self.add(reference_id='s:77',reference_data=bad))
        self.assertTrue(self.add(reference_id='s:77',reference_data=source))
        history=self.store.history(self.store.claim(),True)
        refs=[x for x in history if x['kind']=='explicit_reference']
        self.assertEqual(len(refs),1)
        self.assertEqual(refs[0]['question'],'普通群消息')
        self.assertEqual(refs[0]['speaker_type'],'member')

    def test_mention_does_not_authorize_other_member_memory(self):
        self.add('1',sender='bob',text='我的选择是蓝色')
        Worker(self.store,Model(),Sender(),'send').run_one()
        self.add('2',mentions=('bot',),text='他的选择是什么')
        job=self.store.claim()
        self.assertFalse(any(x['kind']=='explicitly_mentioned_person_history' for x in self.store.history(job,True)))
        self.store.transition(job['id'],'generating','preview')
        self.add('3',mentions=('bot','bob'),text='他的选择是什么')
        job=self.store.claim()
        related=[x for x in self.store.history(job,True) if x['kind']=='explicitly_mentioned_person_history']
        self.assertEqual(related,[])
        self.store.forget('room','bob')
        self.assertFalse(any(x['kind']=='explicitly_mentioned_person_history' for x in self.store.history(job,True)))
    def test_preview_never_sends(self):
        self.add(); sender=Sender()
        Worker(self.store,Model(),sender).run_one()
        self.assertEqual(sender.calls,0)
        self.assertEqual(self.store.status(),{'preview':1})
    def test_bad_target_never_sends(self):
        self.add(); sender=Sender(valid=False)
        Worker(self.store,Model(),sender,'send').run_one()
        self.assertEqual(sender.calls,0)
    def test_timeout_not_retried_even_after_restart(self):
        self.add(); sender=Sender(fail=True)
        Worker(self.store,Model(),sender,'send').run_one()
        self.store.close(); self.store=Store(self.path,'bot',{'room'},100)
        self.store.recover()
        self.assertFalse(Worker(self.store,Model(),sender,'send').run_one())
        self.assertEqual(sender.calls,1)
        self.assertEqual(self.store.status(),{'uncertain':1})
    def test_crash_during_send_never_retries(self):
        self.add(); job=self.store.claim()
        self.store.transition(job['id'],'generating','sending')
        self.store.recover()
        self.assertIsNone(self.store.claim())
    def test_context_separates_senders_and_default_is_off(self):
        self.add(); Worker(self.store,Model(),Sender(),'send').run_one()
        self.add('2',sender='bob'); bob=self.store.claim()
        self.assertEqual(self.store.history(bob,True),[])
        self.add('3'); alice=self.store.claim()
        self.assertEqual(self.store.history(alice),[])
        self.assertEqual(len(self.store.history(alice,True)),1)
    def test_bad_receipt_is_uncertain(self):
        class Bad(Sender):
            def send_and_verify(self,*args):
                return {'account':'bot','group':'wrong','message_id':'x','text_sha256':'x'}
        self.add(); Worker(self.store,Model(),Bad(),'send').run_one()
        self.assertEqual(self.store.status(),{'uncertain':1})

    def test_long_gap_keeps_own_history_without_expanding_others(self):
        self.add(text='我的预算是两千元'); Worker(self.store,Model(),Sender(),'send').run_one()
        self.add('2',sender='bob',text='我的预算是一万元')
        Worker(self.store,Model(),Sender(),'send').run_one()
        self.assertTrue(self.store.ingest(Message('bot','room','3','alice',('bot',),
            '结合他的方案和我的预算',100000,reference_id='2'),now=100001))
        history=self.store.history(self.store.claim(),True)
        own=[x for x in history if x['kind']=='current_person_history']
        ref=[x for x in history if x['kind']=='explicit_reference']
        self.assertEqual(len(own),1); self.assertEqual(ref,[])
        self.assertIn('两千',own[0]['question'])
        self.assertNotIn('一万',json.dumps(history,ensure_ascii=False))

    def test_reference_does_not_cross_group_and_forget_removes_context(self):
        self.add(); Worker(self.store,Model(),Sender(),'send').run_one()
        self.store.forget('room','alice')
        self.add('2',reference_id='1'); history=self.store.history(self.store.claim(),True)
        self.assertEqual(history,[])  # forgetting also disables subsequent memory

    def test_explicit_memory_requires_evidence_and_confirmed_delivery(self):
        self.add(text='本次预算3500元'); job=self.store.claim()
        self.store.propose_facts(job,[{'key':'本次预算','value':'3500元','evidence':'本次预算3500元'},
            {'key':'不存在','value':'10000元','evidence':'虚构的内容'}])
        with self.assertRaises(ValueError): self.store.commit_facts(job)
        self.store.transition(job['id'],'generating','confirmed',answer='已记住')
        self.store.commit_facts(job)
        self.add('2'); following=self.store.claim()
        facts=[f for item in self.store.history(following,True) for f in item.get('facts',[])]
        self.assertEqual(len(facts),1); self.assertEqual(facts[0]['value'],'3500元')
        self.store.forget('room','alice')
        self.assertEqual(self.store.history(following,True),[])

    def test_claim_never_takes_other_group_jobs(self):
        other=Store(self.path,'bot',{'other'},100)
        other.ingest(Message('bot','other','x','alice',('bot',),'其他群',101),now=102)
        self.assertIsNone(self.store.claim()); other.close()

    def test_model_retry_is_bounded_before_any_send(self):
        class Broken:
            def answer(self,*args): raise TimeoutError('synthetic')
        self.add(); sender=Sender(); worker=Worker(self.store,Broken(),sender,'send')
        for attempt in range(3):
            worker.run_one()
            if attempt<2:
                self.assertEqual(sender.calls,0)
                self.store.db.execute('UPDATE jobs SET next_attempt=0')
        self.assertEqual(sender.calls,1)
        self.assertEqual(self.store.status(),{'confirmed':1})

    def test_ready_survives_restart_without_recomputing(self):
        self.add(); sender=Sender(valid=False)
        Worker(self.store,Model(),sender,'send').run_one(); self.store.recover()
        class NoCall:
            def answer(self,*args): raise AssertionError('must reuse prepared answer')
        self.store.db.execute('UPDATE jobs SET next_attempt=0')
        Worker(self.store,NoCall(),Sender(),'send').run_one()
        self.assertEqual(self.store.status(),{'confirmed':1})

    def test_late_receipt_reconciles_without_send(self):
        self.add(); sender=Sender(fail=True); worker=Worker(self.store,Model(),sender,'send')
        worker.run_one()
        sender.find_receipt=lambda account,room,digest,since:dict(account=account,group=room,message_id='late',text_sha256=digest)
        worker.reconcile()
        self.assertEqual(sender.calls,1); self.assertEqual(self.store.status(),{'confirmed':1})

    def test_forget_cannot_be_undone_by_old_inflight_memory(self):
        self.add(text='预算2000元'); old=self.store.claim()
        self.store.forget('room','alice')
        self.store.propose_facts(old,[{'key':'预算','value':'2000元','evidence':'预算2000元'}])
        self.store.transition(old['id'],'generating','confirmed',answer='旧回答')
        self.store.commit_facts(old)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM facts').fetchone()[0],0)
        self.assertEqual(self.store.db.execute('SELECT question FROM jobs').fetchone()[0],'')

    def test_memory_controls_are_person_scoped_and_skip_model(self):
        self.add(text='忘记我')
        class NoCall:
            def answer(self,*args): raise AssertionError('control must be local')
        Worker(self.store,NoCall(),Sender(),'send').run_one()
        self.assertFalse(self.store.memory_setting('room','alice')[0])
        self.assertTrue(self.store.memory_setting('room','bob')[0])
        self.add('2',text='恢复记忆')
        Worker(self.store,NoCall(),Sender(),'send').run_one()
        self.assertTrue(self.store.memory_setting('room','alice')[0])

    def test_relevant_old_history_found_after_many_intervening_turns(self):
        self.add(text='我的显卡是RTX4090'); Worker(self.store,Model(),Sender(),'send').run_one()
        for i in range(2,13):
            self.add(str(i),text='今天练习'+str(i)); Worker(self.store,Model(),Sender(),'send').run_one()
        self.add('13',text='显卡是什么'); history=self.store.history(self.store.claim(),True)
        self.assertTrue(any('RTX4090' in x.get('question','') for x in history))

class EvidenceTests(unittest.TestCase):
    def reply(self,events,category='verified',url='https://example.org/news'):
        return checked_reply(json.dumps({'category':category,'answer':'候选外部事实',
            'sources':[{'url':url,'as_of':'2026-11-07'}]}),events,'测试时间')
    def test_model_claim_without_tool_evidence_is_blocked(self):
        self.assertNotIn('候选外部事实',self.reply([]))
    def test_unvisited_source_is_blocked(self):
        self.assertNotIn('候选外部事实',self.reply([{'action':{'url':'https://other.org'}}]))
    def test_observed_source_and_timestamp_rendered(self):
        reply=self.reply([{'action':{'type':'openPage','url':'https://example.org/news'}}])
        self.assertIn('候选外部事实',reply); self.assertNotIn('https://example.org/news',reply)
        self.assertNotIn('核查时间',reply); self.assertNotIn('信息时间',reply)
    def test_unavailable_does_not_leak_stale_candidate(self):
        self.assertNotIn('候选外部事实',self.reply([],category='unavailable'))

if __name__=='__main__':
    unittest.main()
