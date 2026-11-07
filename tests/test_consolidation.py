"""Synthetic end-to-end boundaries for supplements and proven-unsent recovery."""
import hashlib,json,time,unittest
from unittest.mock import Mock,patch
import test_core
from core import Store,Worker,DeliveryNotAttempted
from desktop_sender import DesktopSender,AdapterError

class SupplementHistory(unittest.TestCase):
    setUp=test_core.PipelineTests.setUp
    tearDown=test_core.PipelineTests.tearDown
    add=test_core.PipelineTests.add

    def original(self):
        self.add();Worker(self.store,test_core.Model(),test_core.Sender(),'send').run_one()
        j=dict(self.store.db.execute('select * from jobs').fetchone())
        reply='基准估值500，假设每股收益和市盈率。'
        receipt=dict(account='bot',group='room',message_id='supplement1',reference_id=j['message_id'],text_sha256=hashlib.sha256(reply.encode()).hexdigest())
        return j,reply,receipt

    def test_followup_and_explicit_reference_get_confirmed_supplement_after_restart(self):
        j,reply,receipt=self.original();before=dict(j)
        self.store.record_supplement(j['id'],reply,receipt)
        self.store.record_supplement(j['id'],reply,receipt)
        self.assertEqual(self.store.db.execute('select count(*) from supplements').fetchone()[0],1)
        self.assertEqual(dict(self.store.db.execute('select * from jobs where id=?',(j['id'],)).fetchone()),before)
        self.store.close();self.store=Store(self.path,'bot',{'room'},100)
        self.add('2',text='基准估值怎么计算？',reference_id='supplement1')
        history=self.store.history(self.store.claim(),True)
        self.assertTrue(any(h.get('kind')=='explicit_reference' and h.get('answer')==reply for h in history))
        self.assertTrue(any(h.get('kind')=='current_person_history' and h.get('answer')==reply for h in history))

    def test_supplement_cannot_cross_member_or_group_or_accept_wrong_receipt(self):
        j,reply,receipt=self.original()
        for change in ({'group':'other'},{'account':'other'},{'reference_id':'wrong'},{'text_sha256':'wrong'},{'message_id':j['receipt_id']}):
            with self.assertRaises(ValueError):self.store.record_supplement(j['id'],reply,{**receipt,**change})
        self.store.record_supplement(j['id'],reply,receipt)
        self.add('2',sender='bob');self.assertFalse(any(h.get('answer')==reply for h in self.store.history(self.store.claim(),True)))

    def test_forget_deletes_supplement_and_prevents_reimport(self):
        j,reply,receipt=self.original();self.store.record_supplement(j['id'],reply,receipt)
        self.store.forget('room','alice')
        self.assertEqual(self.store.db.execute('select count(*) from supplements').fetchone()[0],0)
        with self.assertRaises(ValueError):self.store.record_supplement(j['id'],reply,receipt)

    def test_supplement_refreshes_keyword_index(self):
        j,reply,receipt=self.original();self.store.record_supplement(j['id'],reply,receipt)
        self.assertIn('估值',self.store.db.execute('select terms from job_search').fetchone()[0])

class PreparedRecovery(unittest.TestCase):
    setUp=test_core.PipelineTests.setUp
    tearDown=test_core.PipelineTests.tearDown
    add=test_core.PipelineTests.add

    def seed(self):
        self.add();j=self.store.claim();digest=hashlib.sha256(b'answer').hexdigest()
        self.store.event(j['id'],'delivery_checkpoint',phase='paste',editor_hash='owned',quote_status='native_verified')
        self.store.event(j['id'],'delivery_pre_send_error',reason='adapter_timeout',retryable=False)
        self.store.transition(j['id'],'generating','attention',answer='answer',answer_hash=digest,error_code='draft_or_target_changed')
        s=test_core.Sender();s.can_resume=Mock(return_value=False);s.prepared_matches=Mock(return_value=True)
        s.resume_prepared=Mock(return_value={'account':'bot','group':'room','message_id':'reply-resumed','reference_id':j['message_id'],'text_sha256':digest})
        return j,s,Worker(self.store,test_core.Model(),s,'send')

    def test_unchanged_owned_quote_can_finish_once(self):
        j,s,w=self.seed();w.reconcile();w.reconcile()
        self.assertEqual(self.store.status(),{'confirmed':1});s.resume_prepared.assert_called_once()

    def test_changed_editor_is_not_cleared_or_sent(self):
        j,s,w=self.seed();s.prepared_matches.return_value=False;w.reconcile()
        self.assertEqual(self.store.status(),{'attention':1});s.resume_prepared.assert_not_called()

    def test_known_unsent_resume_failure_never_loops(self):
        j,s,w=self.seed();s.resume_prepared.side_effect=DeliveryNotAttempted('blocked');w.reconcile();w.reconcile()
        self.assertEqual(self.store.status(),{'attention':1});s.resume_prepared.assert_called_once()

    def test_lost_send_response_stays_uncertain_across_restart(self):
        j,s,w=self.seed();s.resume_prepared.side_effect=TimeoutError();w.reconcile()
        self.store.recover();w.reconcile()
        self.assertEqual(self.store.status(),{'uncertain':1});s.resume_prepared.assert_called_once()

    def test_past_uncertainty_and_stale_evidence_block_auto_resume(self):
        j,s,w=self.seed();self.store.audit(j['id'],{'category':'verified'})
        self.store.db.execute('update model_audits set created=?',(time.time()-121,));w.reconcile();s.resume_prepared.assert_not_called()
        self.store.db.execute('delete from model_audits');self.store.event(j['id'],'uncertain');w.reconcile();s.resume_prepared.assert_not_called()

    def test_resume_rechecks_editor_and_body_without_modifying_user_draft(self):
        s=DesktopSender.__new__(DesktopSender);s.verify_target=Mock(return_value=True);s.source_info=Mock(return_value={'source_state':'available'})
        s.ui=Mock(return_value={'editor_hash':'owned','input_bytes':0})
        j={'account':'bot','room':'room','answer_hash':'expected'};point={'phase':'paste','quote_status':'native_verified','editor_hash':'owned'}
        self.assertTrue(s.prepared_matches(j,point))
        s.ui.return_value={'editor_hash':'owned','input_bytes':8}
        self.assertFalse(s.prepared_matches(j,point))
        s.ui.return_value={'editor_hash':'changed','input_bytes':0}
        self.assertFalse(s.prepared_matches(j,point))

    def test_adapter_error_uses_fixed_code_not_raw_message(self):
        self.assertEqual(AdapterError('paste','Editor fingerprint changed').code,'editor_changed')
        self.assertNotIn('private content',str(AdapterError('paste','private content')))

if __name__=='__main__':unittest.main()
