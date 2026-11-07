import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from native_sender import NativeSender
from core import SourceRevoked

from native_gateway import plain_send, recover_receipt
from protocol_transport import digest, TransportError


class NativeSenderTests(unittest.TestCase):
    def test_recovery_requires_exact_durable_job_and_quote(self):
        import hashlib
        db=sqlite3.connect(':memory:');db.execute('CREATE TABLE intents(job TEXT PRIMARY KEY,fingerprint TEXT,state TEXT)')
        job={'id':1,'account':'self','room':'group','message_id':'s:12','answer_hash':hashlib.sha256(b'answer').hexdigest()}
        key='candidate-'+digest(['hub','native','group','1'])
        message={'id':'local','accountId':'native','threadId':'group','direction':'outgoing','serverId':'77','body':'answer','type':49,'appMessageType':57,'quote':{'serverId':'12'}}
        calls=[]
        def request(path,*args):
            self.assertFalse(args);calls.append(path)
            return {'accountId':'native','destination':'group','state':'accepted','localMessageId':'local','serverId':'77'}
        t=SimpleNamespace(db=db,server_id='hub',account='native',room='group',base='/account',request=request,message=lambda mid:message)
        try:
            self.assertIsNone(recover_receipt(t,job));self.assertEqual(calls,[])
            db.execute('INSERT INTO intents VALUES(?,?,?)',(key,'fp','submission_started'));db.commit()
            self.assertEqual(recover_receipt(t,job)['message_id'],'s:77')
            self.assertIsNone(recover_receipt(t,{**job,'id':2}))
            message['quote']['serverId']='99'
            with self.assertRaisesRegex(RuntimeError,'receipt_mismatch'):recover_receipt(t,job)
            def unavailable(*args):raise TransportError('http_404')
            t.request=unavailable
            self.assertIsNone(recover_receipt(t,job))
        finally:db.close()

    def test_body_time_lookup_is_disabled(self):
        sender=NativeSender({'account':'self','group':'group'})
        with patch.object(sender,'call') as call:
            self.assertIsNone(sender.find_receipt('self','group','hash',0));call.assert_not_called()

    def test_unknown_native_intent_does_not_block_unrelated_reply_or_resend(self):
        import test_core
        from core import Worker
        fixture=test_core.PipelineTests();fixture.setUp()
        try:
            fixture.add();sender=test_core.Sender(fail=True);sender.independent_intents=True
            worker=Worker(fixture.store,test_core.Model(),sender,'send')
            worker.run_one()
            self.assertTrue(fixture.store.delivery_blocked())
            self.assertFalse(fixture.store.delivery_blocked(True))
            fixture.add('2',sender='bob');sender.fail=False
            self.assertTrue(worker.run_one())
            self.assertEqual(fixture.store.status(),{'confirmed':1,'uncertain':1})
            self.assertEqual(sender.calls,2)
            self.assertFalse(worker.run_one());self.assertEqual(sender.calls,2)
            fixture.store.db.execute("UPDATE jobs SET state='attention' WHERE state='uncertain'")
            self.assertTrue(fixture.store.delivery_blocked(True))
        finally:fixture.tearDown()

    def test_worker_reconciliation_uses_job_identity(self):
        import test_core
        from core import Worker
        from unittest.mock import Mock
        fixture=test_core.PipelineTests();fixture.setUp()
        try:
            fixture.add();sender=test_core.Sender(fail=True)
            sender.find_receipt=Mock(side_effect=AssertionError('unsafe_generic_lookup'))
            sender.find_job_receipt=Mock(return_value=None)
            worker=Worker(fixture.store,test_core.Model(),sender,'send')
            worker.run_one();worker.reconcile()
            self.assertEqual(fixture.store.status(),{'uncertain':1})
            sender.find_job_receipt.assert_called_once()
            self.assertIn('id',sender.find_job_receipt.call_args.args[0])
            self.assertEqual(sender.calls,1)
        finally:fixture.tearDown()

    def setUp(self):
        self.sender=NativeSender({'account':'self','group':'group'})

    def test_wrong_scope_never_calls_bridge(self):
        with patch.object(self.sender,'call') as call:
            self.assertFalse(self.sender.verify_target('other','group'))
            call.assert_not_called()

    def test_exact_target_requires_positive_response(self):
        with patch.object(self.sender,'call',return_value={'verified':False}):
            self.assertFalse(self.sender.verify_target('self','group'))

    def test_gateway_uncertainty_is_not_safe_to_retry(self):
        result=SimpleNamespace(returncode=1,stdout=json.dumps({'error':'native_delivery_uncertain'}))
        with patch('native_sender.subprocess.run',return_value=result):
            with self.assertRaisesRegex(RuntimeError,'native_delivery_uncertain'):
                self.sender.call('send')
        self.assertFalse(hasattr(self.sender,'can_resume'))

    def test_authoritative_pre_submission_failure_is_terminal_and_does_not_block(self):
        import test_core
        from core import Worker,NativeDeliveryRejected
        result=SimpleNamespace(returncode=1,stdout=json.dumps({'error':'native_transport_before_submission'}))
        with patch('native_sender.subprocess.run',return_value=result):
            with self.assertRaises(NativeDeliveryRejected):self.sender.call('send')
        fixture=test_core.PipelineTests();fixture.setUp()
        try:
            fixture.add();sender=test_core.Sender()
            sender.send_and_verify=lambda *args: (_ for _ in ()).throw(NativeDeliveryRejected('native_transport_before_submission'))
            Worker(fixture.store,test_core.Model(),sender,'send').run_one()
            self.assertEqual(fixture.store.status(),{'failed':1})
            self.assertFalse(fixture.store.delivery_blocked())
        finally:fixture.tearDown()

    def test_revoked_source_is_terminal(self):
        result=SimpleNamespace(returncode=1,stdout=json.dumps({'error':'source_revoked'}))
        with patch('native_sender.subprocess.run',return_value=result):
            with self.assertRaises(SourceRevoked):self.sender.call('source')

    def test_plain_retry_reuses_original_intent(self):
        db=sqlite3.connect(':memory:')
        db.execute('CREATE TABLE intents(job TEXT PRIMARY KEY,fingerprint TEXT,state TEXT)')
        calls=[]
        def request(path,body=None,key=None):
            if body is not None:calls.append(body);return {}
            return {'accountId':'native','destination':'group','state':'accepted','localMessageId':'local','serverId':'77'}
        t=SimpleNamespace(db=db,server_id='hub',account='native',room='group',base='/account',request=request,
            message=lambda mid:{'accountId':'native','threadId':'group','body':'summary','direction':'outgoing','serverId':'77'})
        try:
            self.assertEqual(plain_send(t,{'id':1},'summary')['state'],'hub_verified')
            self.assertEqual(plain_send(t,{'id':1},'summary')['state'],'hub_verified')
            self.assertEqual(len(calls),1)
            with self.assertRaisesRegex(RuntimeError,'job_changed'):plain_send(t,{'id':1},'changed')
            self.assertEqual(len(calls),1)
        finally:db.close()

    def test_plain_mismatched_receipt_is_not_confirmed(self):
        db=sqlite3.connect(':memory:');db.execute('CREATE TABLE intents(job TEXT PRIMARY KEY,fingerprint TEXT,state TEXT)')
        t=SimpleNamespace(db=db,server_id='hub',account='native',room='group',base='/account',
            request=lambda *a,**k:{'accountId':'native','destination':'group','state':'accepted','localMessageId':'local','serverId':'77'},
            message=lambda mid:{'accountId':'native','threadId':'wrong','body':'summary','direction':'outgoing','serverId':'77'})
        try:
            with self.assertRaisesRegex(RuntimeError,'receipt_mismatch'):plain_send(t,{'id':1},'summary')
        finally:db.close()

if __name__=='__main__':unittest.main()
