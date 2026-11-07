"""Regression tests for observed audit failures; all messages are synthetic."""
import base64
import hashlib
import json
import os
import subprocess
import sys
import time
import unittest
from unittest.mock import patch
import test_core
from test_core import Model,Sender
from core import Worker
from collector import Collector
from codex_client import checked_reply,evidence_urls
from media_context import isolated_prepare

class ResilienceTests(unittest.TestCase):
    setUp=test_core.PipelineTests.setUp
    tearDown=test_core.PipelineTests.tearDown
    add=test_core.PipelineTests.add
    def test_uncertain_blocks_another_member(self):
        self.add(); sender=Sender(fail=True)
        Worker(self.store,Model(),sender,'send').run_one()
        self.add('2',sender='bob')
        self.assertFalse(Worker(self.store,Model(),sender,'send').run_one())
        self.assertEqual(sender.calls,1)

    def test_attention_blocks_send(self):
        self.add();job=self.store.claim()
        self.store.transition(job['id'],'generating','attention')
        self.add('2',sender='bob');sender=Sender()
        self.assertFalse(Worker(self.store,Model(),sender,'send').run_one())
        self.assertEqual(sender.calls,0)

    def test_old_ready_epoch_regenerates(self):
        self.add();sender=Sender(valid=False)
        Worker(self.store,Model(),sender,'send').run_one()
        self.store.forget('room','alice')
        self.store.db.execute('UPDATE jobs SET next_attempt=0')
        sender.valid=True
        Worker(self.store,Model(),sender,'send').run_one()
        self.assertEqual(sender.calls,0)
        self.assertEqual(self.store.status(),{'pending':1})

    def test_retry_preserves_person_order_without_blocking_others(self):
        self.add();job=self.store.claim()
        self.store.transition(job['id'],'generating','retry',next_attempt=time.time()+100)
        self.add('2');self.add('3',sender='bob')
        self.assertEqual(self.store.claim()['sender'],'bob')
        self.assertIsNone(self.store.claim())

    def test_receipt_cannot_confirm_two_jobs(self):
        class SameReceipt(Sender):
            def send_and_verify(self,a,g,text):
                self.calls+=1
                return dict(account=a,group=g,message_id='same',text_sha256=hashlib.sha256(text.encode()).hexdigest())
        sender=SameReceipt()
        self.add();Worker(self.store,Model(),sender,'send').run_one()
        self.add('2');Worker(self.store,Model(),sender,'send').run_one()
        self.assertEqual(self.store.status(),{'confirmed':1,'uncertain':1})

    def test_transition_rolls_back_when_index_fails(self):
        self.add();job=self.store.claim()
        with patch.object(self.store,'index_job',side_effect=RuntimeError('synthetic')):
            with self.assertRaises(RuntimeError):
                self.store.transition(job['id'],'generating','confirmed',receipt_id='new')
        row=self.store.db.execute('SELECT state,receipt_id FROM jobs').fetchone()
        self.assertEqual(tuple(row),('generating',None))

    def test_nan_timestamp_rejected(self):
        self.assertFalse(self.add(created=float('nan')))
        self.assertTrue(self.add('2'))

    def test_native_quote_reserved_for_ambiguous_or_referenced_question(self):
        sender=Sender();sender.current_job=None
        self.add();Worker(self.store,Model(),sender,'send').run_one()
        self.assertFalse(sender.current_job['native_quote_needed'])
        self.add('2',sender='bob');Worker(self.store,Model(),sender,'send').run_one()
        self.assertTrue(sender.current_job['native_quote_needed'])
        self.add('3',created=501,reference_id='s:77')
        # ingest rejects future timestamps; insert an eligible later synthetic row.
        from core import Message
        self.store.ingest(Message('bot','room','later','alice',('bot',),'引用的问题',501,reference_id='s:77'),now=502)
        Worker(self.store,Model(),sender,'send').run_one()
        self.assertTrue(sender.current_job['native_quote_needed'])

    def test_rejected_generation_does_not_store_proposals(self):
        class Invalid(Model):
            last_memories=[dict(key='budget',value='secret',evidence='secret')]
            def answer(self,*args):return 'x'*4001
        self.add(text='secret')
        Worker(self.store,Invalid(),Sender(),'send').run_one()
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM proposed_facts').fetchone()[0],0)

    def test_memory_view_does_not_dump_values(self):
        self.add(text='查看我的记忆');job=self.store.claim()
        self.store.db.execute('INSERT INTO facts VALUES(?,?,?,?,?,?,?)',('bot','room','alice','private','DO_NOT_PUBLISH','1',101))
        reply=self.store.memory_control(job)
        self.assertIn('1项',reply)
        self.assertNotIn('DO_NOT_PUBLISH',reply)

    def test_silent_collector_is_terminated(self):
        collector=Collector(self.path,dict(account='bot',group='room'),100)
        proc=subprocess.Popen([sys.executable,'-c','import time; time.sleep(20)'],stdout=subprocess.PIPE,
            encoding='utf-8',creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        try:
            with self.assertRaises(TimeoutError):collector.consume(self.store,proc,timeout=.15)
            self.assertIsNotNone(proc.poll())
        finally:proc.stdout.close()

class EvidenceAndMediaTests(unittest.TestCase):
    def test_only_explicit_public_open_is_evidence(self):
        url='https://example.com/source'
        self.assertFalse(evidence_urls([{'action':{'type':'search'},'results':[{'url':url}]}]))
        self.assertFalse(evidence_urls([{'status':'failed','action':{'type':'openPage','url':url}}]))
        self.assertFalse(evidence_urls([{'action':{'type':'openPage','url':'http://127.0.0.1/private'}}]))
        self.assertEqual(len(evidence_urls([{'action':{'type':'openPage','url':url}}])),1)

    def test_long_answer_retains_verified_source(self):
        url='https://example.com/source'
        answer=checked_reply(json.dumps(dict(category='verified',answer='a'*6000,sources=[dict(url=url,as_of='today')])),
            [{'action':{'type':'openPage','url':url}}],'now',include_sources=True)
        self.assertLessEqual(len(answer),4000)
        self.assertIn(url,answer)

    def test_parser_subprocess_and_timeout(self):
        data=dict(data=base64.b64encode(b'hello').decode(),kind='file',name='test.txt')
        self.assertIn('hello',json.dumps(isolated_prepare(data)))
        with self.assertRaises(subprocess.TimeoutExpired):isolated_prepare(data,timeout=.001)

if __name__=='__main__':unittest.main()
