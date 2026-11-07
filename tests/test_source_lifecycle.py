"""Withdrawal and retry boundaries with synthetic messages only."""
import json
import unittest
from unittest.mock import Mock
import test_core
from core import Worker, SourceRevoked, DeliveryNotAttempted
from inbox import source_revoked
from desktop_sender import DesktopSender


class SourceLifecycle(unittest.TestCase):
    setUp=test_core.PipelineTests.setUp
    tearDown=test_core.PipelineTests.tearDown
    add=test_core.PipelineTests.add

    def test_revoke_cancels_before_model_and_does_not_block_next_member(self):
        self.add();self.add('2',sender='bob')
        s=test_core.Sender();s.source_info=Mock(side_effect=[{'source_state':'revoked'},{'source_state':'available'}])
        model=Mock();model.answer.return_value='synthetic';model.last_audit={};model.last_memories=[];model.last_attachments=[]
        w=Worker(self.store,model,s,'send');w.run_one()
        model.answer.assert_not_called();self.assertEqual(s.calls,0)
        w.run_one();self.assertEqual(self.store.status(),{'cancelled':1,'confirmed':1})
        self.store.recover();self.assertFalse(w.run_one());self.assertEqual(s.calls,1)

    def test_withdrawn_during_generation_never_retries_or_sends(self):
        self.add();s=test_core.Sender();s.source_info=Mock(return_value={'source_state':'available'})
        s.send_and_verify=Mock(side_effect=SourceRevoked())
        w=Worker(self.store,test_core.Model(),s,'send');w.run_one()
        self.assertEqual(self.store.status(),{'cancelled':1});self.assertFalse(w.run_one())
        self.assertEqual(self.store.db.execute('select error_code from jobs').fetchone()[0],'source_revoked')

    def test_read_failure_is_not_withdrawal_and_has_bounded_retries(self):
        self.add();s=test_core.Sender();s.source_info=Mock(side_effect=TimeoutError('sensitive text'))
        m=Mock();w=Worker(self.store,m,s,'send')
        for _ in range(3):
            self.store.db.execute('update jobs set next_attempt=0');w.run_one()
        self.assertEqual(self.store.status(),{'failed':1});self.assertEqual(s.calls,0);m.answer.assert_not_called()
        events=' '.join(r[0] for r in self.store.db.execute('select metadata from job_events'))
        self.assertNotIn('sensitive text',events)

    def test_precise_pre_send_reason_survives_without_exception_text(self):
        self.add();s=test_core.Sender();s.last_quote_status='source_copy_mismatch'
        s.send_and_verify=Mock(side_effect=DeliveryNotAttempted('private source',True,'native_quote_unavailable'))
        Worker(self.store,test_core.Model(),s,'send').run_one()
        e=json.loads(self.store.db.execute("select metadata from job_events where event='delivery_pre_send_error'").fetchone()[0])
        self.assertEqual(e['quote_status'],'source_copy_mismatch');self.assertTrue(e['retryable'])
        self.assertNotIn('private source',json.dumps(e))

    def test_forget_clears_cancelled_question_and_answer(self):
        self.add();j=self.store.claim();self.store.transition(j['id'],'generating','cancelled',answer='synthetic')
        self.store.forget('room','alice')
        r=self.store.db.execute('select question,answer from jobs').fetchone()
        self.assertEqual(tuple(r),('',None))

    def test_revoke_is_a_database_event_not_a_user_keyword(self):
        for kind,body,expected in [(10000,'某成员撤回了一条消息',True),(10002,'<revokemsg/>',True),
                (1,'请解释撤回消息',False),(49,'<revokemsg/>',False),(10000,'某成员加入群聊',False)]:
            self.assertEqual(source_revoked({'local_type':kind,'message_content':body}),expected)

    def test_revoked_source_never_touches_ui(self):
        s=DesktopSender.__new__(DesktopSender);s.ui=Mock()
        s.source_info=Mock(return_value={'source_state':'revoked','unique':False})
        with self.assertRaises(SourceRevoked):
            s.prepare_quote({'account':'bot','room':'room','message_id':'s:1','question':'synthetic'})
        s.ui.assert_not_called()

if __name__=='__main__':unittest.main()
