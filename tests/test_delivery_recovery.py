"""Synthetic fault injection; never sends real WeChat messages."""
import base64,io,json,sys,types,unittest,time
from unittest.mock import Mock,patch
from PIL import Image,ImageDraw
import test_core
from core import Worker,DeliveryNotAttempted
from desktop_sender import DesktopSender,source_candidates,AdapterError

class QueueRecovery(unittest.TestCase):
    setUp=test_core.PipelineTests.setUp
    tearDown=test_core.PipelineTests.tearDown
    add=test_core.PipelineTests.add
    def test_safe_failure_retries_then_other_person_continues(self):
        class Flaky(test_core.Sender):
            def send_and_verify(self,a,g,text):
                if self.current_job['sender']=='alice':raise DeliveryNotAttempted('no send',retryable=True)
                return super().send_and_verify(a,g,text)
        s=Flaky();s.current_job=None;w=Worker(self.store,test_core.Model(),s,'send')
        self.add()
        for _ in range(3):
            self.store.db.execute('update jobs set next_attempt=0');w.run_one()
        self.assertEqual(self.store.status(),{'failed':1})
        self.add('2',sender='bob');w.run_one()
        self.assertEqual(self.store.status(),{'failed':1,'confirmed':1})
    def test_attention_recovers_only_after_whole_editor_is_clear(self):
        self.add();j=self.store.claim();self.store.transition(j['id'],'generating','attention',error_code='draft_or_target_changed')
        s=test_core.Sender();s.can_resume=Mock(return_value=False)
        w=Worker(self.store,test_core.Model(),s,'send');w.reconcile()
        self.assertEqual(self.store.status(),{'attention':1})
        s.can_resume.return_value=True;w.reconcile()
        self.assertEqual(self.store.status(),{'ready':1});self.assertEqual(s.calls,0)
    def test_uncertain_never_released_by_empty_editor(self):
        self.add();j=self.store.claim();self.store.transition(j['id'],'generating','uncertain')
        s=test_core.Sender();s.can_resume=Mock(return_value=True)
        Worker(self.store,test_core.Model(),s,'send').reconcile()
        self.assertEqual(self.store.status(),{'uncertain':1});s.can_resume.assert_not_called()
    def test_receipt_timeout_then_restart_never_resends(self):
        self.add();s=test_core.Sender(fail=True)
        s.find_receipt=Mock(return_value=None);s.can_resume=Mock(return_value=True)
        w=Worker(self.store,test_core.Model(),s,'send');w.run_one()
        self.store.recover();w.reconcile()
        self.add('2',sender='bob');self.assertFalse(w.run_one())
        self.assertEqual(s.calls,1);self.assertEqual(self.store.status(),{'uncertain':1,'pending':1})
    def test_wrong_room_receipt_is_never_accepted(self):
        class WrongRoom(test_core.Sender):
            def send_and_verify(self,a,g,text):
                receipt=super().send_and_verify(a,g,text);receipt['group']='different';return receipt
        self.add();s=WrongRoom();w=Worker(self.store,test_core.Model(),s,'send');w.run_one()
        self.assertEqual(self.store.status(),{'uncertain':1});self.assertFalse(w.run_one())
    def test_other_members_can_continue_during_safe_retry_delay(self):
        class Flaky(test_core.Sender):
            def send_and_verify(self,a,g,text):
                if self.current_job['sender']=='alice':raise DeliveryNotAttempted('clean editor',retryable=True)
                return super().send_and_verify(a,g,text)
        s=Flaky();s.current_job=None;self.add();self.add('2',sender='bob')
        w=Worker(self.store,test_core.Model(),s,'send');w.run_one();w.run_one()
        self.assertEqual(self.store.status(),{'ready':1,'confirmed':1})
    def test_old_attention_expires_without_replaying(self):
        self.store.max_queue_age=1800;self.add();j=self.store.claim(now=102)
        self.store.transition(j['id'],'generating','attention',error_code='draft_or_target_changed')
        s=test_core.Sender();s.can_resume=Mock(return_value=True)
        Worker(self.store,test_core.Model(),s,'send').reconcile()
        self.assertEqual(self.store.status(),{'expired':1});self.assertEqual(s.calls,0)
    def test_summary_uses_window_deadline_not_ordinary_thirty_minutes(self):
        self.store.max_queue_age=1800
        job={'sender':'__scheduled_group_summary__','message_id':'summary:10000','created':10005}
        self.assertFalse(self.store.expired(job,13000))
        self.assertTrue(self.store.expired(job,13601))
        job['sender']='alice';self.assertTrue(self.store.expired(job,13000))

class AdapterGuards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Import pure adapter guards without requiring a Linux X server.
        fake=types.ModuleType('Xlib');fake.display=Mock();fake.X=Mock()
        with patch.dict(sys.modules,{'Xlib':fake}):
            import desktop_io
        cls.adapter=desktop_io
    def capture(self,quote=False):
        im=Image.new('RGB',(980,710),(240,240,240))
        if quote:ImageDraw.Draw(im).rectangle((300,505,600,555),fill=(220,220,220))
        buf=io.BytesIO();im.save(buf,format='PNG')
        return {'image':base64.b64encode(buf.getvalue()).decode(),'view_hash':'current'}
    def test_empty_text_with_native_quote_is_not_blank(self):
        a=self.adapter;g={'WIDTH':980,'HEIGHT':710}
        with patch.object(a,'window_capture',return_value=self.capture(False)):self.assertTrue(a.editor_state(g)['blank'])
        with patch.object(a,'window_capture',return_value=self.capture(True)):self.assertFalse(a.editor_state(g)['blank'])
    def test_end_of_selected_text_caret_blink_does_not_change_fingerprint(self):
        a=self.adapter
        im=Image.new('RGB',(980,710),(237,237,237))
        ImageDraw.Draw(im).rectangle((300,505,600,555),fill=(220,220,220))
        blink=im.copy();ImageDraw.Draw(blink).rectangle((711,476,712,497),fill=(7,193,96))
        self.assertEqual(a.stable_editor(im).tobytes(),a.stable_editor(blink).tobytes())
        altered=im.copy();ImageDraw.Draw(altered).rectangle((320,510,330,520),fill=(80,80,80))
        self.assertNotEqual(a.stable_editor(im).tobytes(),a.stable_editor(altered).tobytes())
    def test_changed_quote_or_user_edit_rejected_before_paste(self):
        a=self.adapter
        with patch.object(a,'editor_state',return_value={'editor_hash':'new'}):
            with self.assertRaisesRegex(RuntimeError,'Editor fingerprint'):a.verify_editor({}, {'expected_editor':'old'})
            with self.assertRaises(RuntimeError):a.verify_editor({}, {})
    def test_stale_menu_coordinates_never_click(self):
        a=self.adapter;g={'WIDTH':980,'HEIGHT':710}
        with patch.object(a,'window',return_value=('1',g)),patch.object(a,'verify_header'),patch.object(a,'window_capture',return_value=self.capture()),patch.object(a,'command') as command:
            with self.assertRaisesRegex(RuntimeError,'Quote view changed'):
                a.perform({'op':'quote_click','x':400,'y':300,'expected_view':'old'})
            command.assert_not_called()

class SourceProvenance(unittest.TestCase):
    def test_ocr_typo_is_only_a_candidate_and_ambiguous_candidates_stay_ambiguous(self):
        line={'text':'@bot synthetic questlon','x':300,'right':600,'y':100,'bottom':120}
        self.assertEqual(len(source_candidates('@bot synthetic question',[line],710)),1)
        self.assertEqual(len(source_candidates('@bot synthetic question',[line,line],710)),2)
        self.assertEqual(source_candidates('hi',[dict(line,text='ho')],710),[])
        self.assertEqual(source_candidates('@bot synthetic question',[dict(line,y=600)],710),[])
    def make_sender(self,copy_matches=True):
        s=DesktopSender.__new__(DesktopSender);s.engine=object();s.expected_editor='blank'
        s.current_job=None
        calls=[]
        cap=AdapterGuards().capture();cap.update(width=980,height=710)
        def ui(op,**kw):
            calls.append((op,kw))
            if op=='quote_view':return cap
            if op=='quote_copy_read':return {'text':'@bot synthetic question' if copy_matches else 'different'}
            if op=='quote_editor':return {'blank':False,'editor_hash':'owned-quote'}
            if op=='check_input':return {'input_bytes':0,'blank':False,'editor_hash':'owned-quote'}
            return {}
        s.ui=ui
        return s,calls
    def test_pale_preview_ocr_not_needed_after_exact_source_and_guarded_clicks(self):
        s,calls=self.make_sender()
        question='@bot synthetic question'
        source=[{'text':question,'x':300,'right':600,'y':100,'bottom':120}]
        copy=[{'text':'复制','x':400,'right':450,'y':130,'bottom':150}]
        quote=[{'text':'引用','x':400,'right':450,'y':160,'bottom':180}]
        scans=iter([source,copy,source,quote])
        async def recognize(*args):return next(scans)
        with patch('desktop_sender.recognize',recognize),patch('desktop_sender.subprocess.run',return_value=types.SimpleNamespace(returncode=0,stdout='{"unique":true}')):
            self.assertTrue(s.prepare_quote({'question':question,'account':'bot','room':'room','message_id':'1'}))
        self.assertEqual(s.expected_editor,'owned-quote')
        clicks=[kw for op,kw in calls if op=='quote_click']
        self.assertEqual(len(clicks),4);self.assertTrue(all(c['expected_view']=='current' for c in clicks))
    def test_wrong_clipboard_never_creates_quote(self):
        s,calls=self.make_sender(False)
        scans=iter([[{'text':'@bot synthetic question','x':300,'right':600,'y':100,'bottom':120}],[{'text':'复制','x':400,'right':450,'y':130,'bottom':150}]])
        async def recognize(*args):return next(scans)
        with patch('desktop_sender.recognize',recognize),patch('desktop_sender.subprocess.run',return_value=types.SimpleNamespace(returncode=0,stdout='{"unique":true}')):
            self.assertFalse(s.prepare_quote({'question':'@bot synthetic question','account':'bot','room':'room','message_id':'1'}))
        self.assertNotIn('quote_editor',[op for op,_ in calls])

    def test_quoted_preview_without_copy_menu_does_not_hide_original_above(self):
        s,calls=self.make_sender()
        source=[{'text':'@bot synthetic question','x':300,'right':600,'y':100,'bottom':120}]
        copy=[{'text':'复制','x':400,'right':450,'y':130,'bottom':150}]
        quote=[{'text':'引用','x':400,'right':450,'y':160,'bottom':180}]
        scans=iter([source,[],source,source,copy,source,quote])
        async def recognize(*args):return next(scans)
        with patch('desktop_sender.recognize',recognize),patch('desktop_sender.subprocess.run',return_value=types.SimpleNamespace(returncode=0,stdout='{"unique":true}')):
            self.assertTrue(s.prepare_quote({'question':'@bot synthetic question','account':'bot','room':'room','message_id':'1'}))
        self.assertEqual(sum(op=='quote_scroll' for op,_ in calls),1)



class SendBoundaryTests(unittest.TestCase):
    def sender(self, fault, changed=False):
        import hashlib
        s=DesktopSender.__new__(DesktopSender);s.expected_editor='owned'
        s.verify_target=Mock(return_value=True);s.find_receipt=Mock(return_value={'message_id':'receipt'})
        digest=hashlib.sha256(b'answer').hexdigest();self.sends=0
        def ui(op,**kw):
            if op=='check_input':return {'input_sha256':digest}
            if op=='quote_editor':return {'editor_hash':'changed' if changed else 'owned'}
            if op=='send':
                self.sends+=1
                if self.sends==1:raise fault
                return {'clicked':True}
        s.ui=Mock(side_effect=ui);return s
    def test_explicit_preclick_rejection_retries_exact_owned_draft_once(self):
        s=self.sender(DeliveryNotAttempted('before click'))
        self.assertEqual(s.send_existing_draft_and_verify('bot','room','answer')['message_id'],'receipt')
        self.assertEqual(self.sends,2)
    def test_changed_quote_is_not_retried(self):
        s=self.sender(DeliveryNotAttempted('before click'),True)
        with self.assertRaises(DeliveryNotAttempted):s.send_existing_draft_and_verify('bot','room','answer')
        self.assertEqual(self.sends,1);s.find_receipt.assert_not_called()
    def test_transport_loss_after_send_is_never_retried(self):
        s=self.sender(TimeoutError('unknown click outcome'))
        with self.assertRaises(TimeoutError):s.send_existing_draft_and_verify('bot','room','answer')
        self.assertEqual(self.sends,1)
    def test_adapter_missing_attempt_flag_is_ambiguous(self):
        s=DesktopSender.__new__(DesktopSender);s.verified_header={}
        for flag,kind in [(False,DeliveryNotAttempted),(True,RuntimeError),(None,RuntimeError)]:
            payload={'error':'synthetic'}
            if flag is not None:payload['send_may_have_occurred']=flag
            with self.subTest(flag=flag),patch('desktop_sender.subprocess.run',return_value=types.SimpleNamespace(returncode=1,stdout=json.dumps(payload))):
                try:s.ui('send')
                except Exception as exc:self.assertIs(type(exc),AdapterError if kind is RuntimeError else kind)
                else:self.fail('error required')

class IntakeAndModelFaults(unittest.TestCase):
    setUp=test_core.PipelineTests.setUp
    tearDown=test_core.PipelineTests.tearDown
    add=test_core.PipelineTests.add
    def test_permanent_model_fault_returns_one_explicit_fallback(self):
        class Offline:
            def answer(self,*args):raise TimeoutError('synthetic provider outage')
        self.add();s=test_core.Sender();w=Worker(self.store,Offline(),s,'send')
        for _ in range(3):
            self.store.db.execute('update jobs set next_attempt=0');w.run_one()
        self.assertEqual(s.calls,1);self.assertEqual(self.store.status(),{'confirmed':1})
        reply=self.store.db.execute('select answer from jobs').fetchone()[0]
        self.assertIn('自动重试后仍未恢复',reply)
        self.assertIn('重新 @ 我',reply)
        self.assertFalse(w.run_one())
    def test_malformed_intake_does_not_drop_next_valid_question(self):
        from collector import Collector
        collector=Collector(self.path,{'account':'bot','group':'room'},100)
        msg=dict(account='bot',group='room',message_id='1',sender='alice',mentions=['bot'],text='synthetic',created=time.time())
        collector.accept(self.store,{'kind':'batch','config':{'account':'bot','group':'room'},'messages':[{'unknown':'bad'},msg]})
        self.assertEqual(self.store.status(),{'pending':1});self.assertTrue(collector.changed.is_set())
        collector.accept(self.store,{'kind':'batch','config':{'account':'bot','group':'room'},'messages':[msg]})
        self.assertEqual(self.store.status(),{'pending':1})
    def test_scope_change_rejected_before_ingest(self):
        from collector import Collector
        collector=Collector(self.path,{'account':'bot','group':'room'},100)
        with self.assertRaises(ValueError):collector.accept(self.store,{'kind':'batch','config':{'account':'bot','group':'other'},'messages':[]})
        self.assertEqual(self.store.status(),{})
    def test_offline_intake_recovers_on_next_heartbeat(self):
        from collector import Collector
        c=Collector(self.path,{'account':'bot','group':'room'},100)
        c.accept(self.store,{'kind':'error'});self.assertFalse(c.snapshot()[0]['healthy'])
        c.accept(self.store,{'kind':'heartbeat'});self.assertTrue(c.snapshot()[0]['healthy'])




class RequiredNativeQuoteTests(unittest.TestCase):
    def test_missing_native_quote_never_falls_back_to_plain_reply(self):
        from test_scenarios import DeliveryMatrix
        s=DeliveryMatrix().sender('回答');s.prepare_quote.return_value=False
        with self.assertRaises(DeliveryNotAttempted):s.send_and_verify('bot','room','回答')
        s.send_existing_draft_and_verify.assert_not_called()
    def test_long_wrapped_source_can_be_located_by_prefix_but_not_authorized_by_it(self):
        text='@bot '+('a long question with detailed context '*12)
        line={'text':text[:55],'x':300,'right':650,'y':100,'bottom':120}
        self.assertEqual(len(source_candidates(text,[line],710)),1)
    def test_missing_source_search_is_bounded(self):
        s,calls=SourceProvenance().make_sender()
        async def recognize(*args):return []
        with patch('desktop_sender.recognize',recognize),patch('desktop_sender.subprocess.run',return_value=types.SimpleNamespace(returncode=0,stdout='{"unique":true}')):
            self.assertFalse(s.prepare_quote({'question':'@bot synthetic question','account':'bot','room':'room','message_id':'1'}))
        self.assertEqual(sum(op=='quote_scroll' for op,_ in calls),6)
        self.assertFalse(any(op=='quote_click' for op,_ in calls))

class NativeReceiptTests(unittest.TestCase):
    setUp=test_core.PipelineTests.setUp
    tearDown=test_core.PipelineTests.tearDown
    add=test_core.PipelineTests.add
    def test_plain_receipt_does_not_confirm_required_quote(self):
        s=test_core.Sender();s.current_job=None;s.native_quotes_enabled=True
        self.add();w=Worker(self.store,test_core.Model(),s,'send');w.run_one()
        self.assertEqual(self.store.status(),{'uncertain':1})
        row=self.store.db.execute('select * from jobs').fetchone()
        s.find_receipt=Mock(return_value={'account':'bot','group':'room','message_id':'plain','text_sha256':row['answer_hash']})
        w.reconcile();self.assertEqual(self.store.status(),{'uncertain':1})
    def test_exact_native_reference_confirms(self):
        class Native(test_core.Sender):
            native_quotes_enabled=True
            current_job=None
            def send_and_verify(self,a,g,text):
                receipt=super().send_and_verify(a,g,text);receipt['reference_id']=self.current_job['message_id'];return receipt
        self.add();Worker(self.store,test_core.Model(),Native(),'send').run_one()
        self.assertEqual(self.store.status(),{'confirmed':1})




class LongEditorReadTests(AdapterGuards):
    def test_empty_editor_cannot_reuse_old_clipboard(self):
        a=self.adapter;calls=[]
        def command(*args,input=None):
            calls.append(args)
            if args==('xclip','-selection','clipboard','-o'):return b'wechat-agent-input-read-sentinel'
            return b''
        with patch.object(a,'click') as click,patch.object(a,'command',side_effect=command),patch.object(a.time,'sleep'):
            self.assertEqual(a.read_input({'X':0,'Y':0,'WIDTH':980,'HEIGHT':710}),b'')
        click.assert_called_once_with({'X':0,'Y':0,'WIDTH':980,'HEIGHT':710},330,480)
        self.assertIn(('xdotool','key','--clearmodifiers','ctrl+End'),calls)
        self.assertTrue(any(args[-1]=='5' for args in calls))
    def test_long_text_readback_keeps_exact_bytes_and_normalizes_scroll(self):
        a=self.adapter;text=('长回复\n'*200).encode()
        def command(*args,input=None):
            return text if args==('xclip','-selection','clipboard','-o') else b''
        with patch.object(a,'click'),patch.object(a,'command',side_effect=command),patch.object(a.time,'sleep'):
            self.assertEqual(a.read_input({'X':0,'Y':0,'WIDTH':980,'HEIGHT':710}),text)




class RepeatedQuestionTests(unittest.TestCase):
    def test_latest_identical_question_can_be_quoted(self):
        s,calls=SourceProvenance().make_sender();base=s.ui
        s.ui=lambda op,**kw: {'at_bottom':True} if op=='quote_latest' else base(op,**kw)
        line={'text':'@bot synthetic question','x':300,'right':600,'y':100,'bottom':120}
        scans=iter([[line,dict(line,y=180,bottom=200)],[{'text':'复制','x':400,'right':450,'y':220,'bottom':240}],[line,dict(line,y=180,bottom=200)],[{'text':'引用','x':400,'right':450,'y':220,'bottom':240}]])
        async def recognize(*args):return next(scans)
        with patch('desktop_sender.recognize',recognize),patch('desktop_sender.subprocess.run',return_value=types.SimpleNamespace(returncode=0,stdout='{"unique":false,"latest_match":true}')):
            self.assertTrue(s.prepare_quote({'question':line['text'],'account':'bot','room':'room','message_id':'newest'}))
        right_clicks=[kw for op,kw in calls if op=='quote_click' and kw.get('right')]
        self.assertEqual([kw['y'] for kw in right_clicks],[190,190])
    def test_cannot_anchor_bottom_does_not_guess(self):
        s,calls=SourceProvenance().make_sender();s.ui=Mock(return_value={'at_bottom':False})
        with patch('desktop_sender.subprocess.run',return_value=types.SimpleNamespace(returncode=0,stdout='{"unique":false,"latest_match":true}')):
            self.assertFalse(s.prepare_quote({'question':'@bot synthetic question','account':'bot','room':'room','message_id':'newest'}))
        self.assertEqual(s.ui.call_count,1)
    def test_old_duplicate_is_not_substituted_with_new_question(self):
        s,calls=SourceProvenance().make_sender()
        with patch('desktop_sender.subprocess.run',return_value=types.SimpleNamespace(returncode=0,stdout='{"unique":false,"latest_match":false}')):
            self.assertFalse(s.prepare_quote({'question':'@bot synthetic question','account':'bot','room':'room','message_id':'old'}))
        self.assertEqual(calls,[])
    def test_new_identical_message_during_copy_aborts_quote(self):
        s,calls=SourceProvenance().make_sender();base=s.ui
        s.ui=lambda op,**kw: {'at_bottom':True} if op=='quote_latest' else base(op,**kw)
        scans=iter([[{'text':'@bot synthetic question','x':300,'right':600,'y':100,'bottom':120}],[{'text':'复制','x':400,'right':450,'y':130,'bottom':150}]])
        async def recognize(*args):return next(scans)
        responses=[types.SimpleNamespace(returncode=0,stdout='{"unique":false,"latest_match":true}'),types.SimpleNamespace(returncode=0,stdout='{"unique":false,"latest_match":false}')]
        with patch('desktop_sender.recognize',recognize),patch('desktop_sender.subprocess.run',side_effect=responses):
            self.assertFalse(s.prepare_quote({'question':'@bot synthetic question','account':'bot','room':'room','message_id':'old'}))
        self.assertNotIn('quote_editor',[op for op,_ in calls])

if __name__=='__main__':unittest.main()

