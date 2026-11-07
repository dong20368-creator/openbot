"""Dedicated-container sender; OCR title + input readback + DB receipt checks."""
import asyncio
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import re
from PIL import Image,ImageOps
import unicodedata
from difflib import SequenceMatcher
from image_ocr import recognize,title_matches
from winrt.windows.globalization import Language
from winrt.windows.media.ocr import OcrEngine
from core import DeliveryNotAttempted,SourceRevoked
from group_summary import is_summary


def quote_text(value):
    return re.sub(r'\s+','',unicodedata.normalize('NFKC',value)).casefold()

def matching_preview(text,lines):
    expected=quote_text(text)
    if not expected:return False
    for i,line in enumerate(lines):
        combined=''
        for j in range(i,min(i+4,len(lines))):
            if j>i and lines[j].get('y',0)-lines[j-1].get('bottom',0)>24:break
            combined+=quote_text(lines[j]['text'])
            if expected in combined and combined.split(expected,1)[0].endswith(':'):return True
    return False


def source_candidates(text,lines,height):
    """OCR proposes a location only. Exact clipboard text authorizes the quote."""
    expected=quote_text(text)
    candidates=[]
    for line in lines:
        if not (line['x']>285 and 65<line['y']<height-260):continue
        observed=quote_text(line['text'])
        prefix=(len(observed)>=20 and len(expected)>len(observed)+8
            and SequenceMatcher(None,expected[:len(observed)],observed).ratio()>=.85)
        if observed==expected or prefix or (len(expected)>=8 and SequenceMatcher(None,expected,observed).ratio()>=.8):
            candidates.append(line)
    return candidates


class AdapterError(RuntimeError):
    """Stable diagnostics only: never persist arbitrary UI/clipboard text."""
    def __init__(self,operation,detail):
        codes={'Editor fingerprint changed':'editor_changed','Target fingerprint changed':'target_changed',
            'Existing draft; input untouched':'existing_draft','Input readback mismatch; not sent':'input_mismatch',
            'Draft changed; not sent':'draft_changed','Quote view changed':'source_view_changed',
            'Main window not unique':'window_ambiguous'}
        self.code=codes.get(detail,'adapter_timeout' if detail.startswith('command_timeout:') else 'adapter_error')
        self.operation=operation
        super().__init__(self.code)

class DesktopSender:
    native_quotes_enabled=True
    def __init__(self,config):
        self.config=config
        self.verified_header=None
        self.last_navigation=0
        self.current_job=None
        self.expected_editor=None
        self.checkpoint_sink=None
        self.engine=OcrEngine.try_create_from_language(Language('zh-Hans'))
        if not self.engine: raise RuntimeError('Chinese OCR unavailable')

    def ui(self,op,**args):
        if op in ('paste','check_input','send','check_empty') or op.startswith('quote_'): args['expected_header']=self.verified_header
        run=subprocess.run(['wsl','-d','WeChatAgent','-u','root','--exec','docker','exec','-i',
            'wechat-agent-v2-desktop-1','python3','/config/agent-desktop-io.py'],
            input=json.dumps({'op':op,**args}),capture_output=True,encoding='utf-8',timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        if run.returncode:
            try:
                failure=json.loads(run.stdout);detail=failure.get('error','unknown')
            except ValueError: failure={};detail='invalid_response'
            if op=='send' and failure.get('send_may_have_occurred') is False:
                raise DeliveryNotAttempted('Adapter rejected before Send',code=AdapterError(op,detail).code)
            raise AdapterError(op,detail)
        return json.loads(run.stdout)

    def checkpoint(self,phase):
        self.last_phase=phase
        sink=getattr(self,'checkpoint_sink',None)
        if sink:sink(phase=phase,editor_hash=self.expected_editor,
            quote_status=getattr(self,'last_quote_status','not_requested'))

    def verify_target(self,account,room):
        if (account,room)!=(self.config['account'],self.config['group']): return False
        captured=self.ui('header')
        image=Image.open(io.BytesIO(base64.b64decode(captured['header_png'])))
        # Only the group title crop is OCRed, never message bodies for identity.
        lines=asyncio.run(recognize(self.engine,image.resize((image.width*2,image.height*2))))
        valid=captured['active'] and any(title_matches(line['text'],self.config['group_name']) for line in lines)
        self.verified_header=captured['signature'] if valid else None
        return valid

    def ensure_target(self,account,room):
        if self.verify_target(account,room): return True
        if (account,room)!=(self.config['account'],self.config['group']): return False
        if time.monotonic()-self.last_navigation<30: return False
        self.last_navigation=time.monotonic()
        result=self.ui('search_group',name=self.config['group_name'])
        image=Image.open(io.BytesIO(base64.b64decode(result['image'])))
        lines=asyncio.run(recognize(self.engine,image.resize((image.width*2,image.height*2))))
        # Search suggestions can repeat the name. Only the actual group-chat
        # section is eligible; never click web-search suggestions.
        sections=[line for line in lines if line['text'].replace(' ','')=='群聊']
        if len(sections)!=1: return False
        start=sections[0]['bottom']
        end=min([line['y'] for line in lines if line['y']>start and line['text'].replace(' ','') in ('聊天记录','联系人','公众号')]+[image.height])
        matches=[line for line in lines if start<line['y']<end and title_matches(line['text'],self.config['group_name'])]
        if len(matches)!=1: return False
        line=matches[0]
        self.ui('select_group',window=result['window'],hash=result['hash'],
            x=(line['x']+line['right'])/2,y=(line['y']+line['bottom'])/2)
        return self.verify_target(account,room)

    def send_and_verify(self,account,room,text):
        self.last_timings={};started=time.monotonic()
        self.expected_editor=None
        self.last_quote_status='not_requested'
        self.checkpoint('target_check')
        try:
            if not self.verify_target(account,room): raise RuntimeError('Wrong target')
            initial=self.ui('check_empty')
            if not initial.get('empty'): raise RuntimeError('Existing draft')
            self.expected_editor=initial.get('editor_hash')
            self.last_timings['target_and_empty_seconds']=round(time.monotonic()-started,3)
            phase=time.monotonic()
            self.checkpoint('quote_prepare')
            if (self.current_job and not is_summary(self.current_job)
                and not self.current_job.get('privacy_warning')):
                if not self.native_quotes_enabled or not self.prepare_quote(self.current_job):
                    raise RuntimeError('Required native quote unavailable')
            self.last_timings['quote_seconds']=round(time.monotonic()-phase,3)
            self.last_timings['quote_status']=getattr(self,'last_quote_status','not_requested')
            self.checkpoint('quote_ready')
            phase=time.monotonic()
            digest=hashlib.sha256(text.encode()).hexdigest()
            self.checkpoint('paste')
            pasted=self.ui('paste',text=text,expected_editor=self.expected_editor)
            if pasted.get('input_sha256')!=digest:
                raise RuntimeError('Wrong draft')
            self.expected_editor=pasted.get('editor_hash')
            self.last_timings['paste_seconds']=round(time.monotonic()-phase,3)
            self.checkpoint('draft_ready')
        except SourceRevoked:
            raise
        except Exception as exc:
            # Retry only if no send was attempted AND the whole editor is blank.
            # Never erase drafts or native references in order to recover.
            clean=self.can_resume(account,room)
            reasons={'Wrong target':'target_changed','Existing draft':'existing_draft',
                'Wrong draft':'input_mismatch','Quote was not created':'quote_not_created',
                'Required native quote unavailable':'native_quote_unavailable'}
            raise DeliveryNotAttempted('Draft preparation failed',retryable=clean,
                code=getattr(exc,'code',reasons.get(str(exc),'adapter_preparation_error'))) from exc
        phase=time.monotonic()
        receipt=self.send_existing_draft_and_verify(account,room,text)
        self.last_timings['send_and_receipt_seconds']=round(time.monotonic()-phase,3)
        return receipt

    def can_resume(self,account,room):
        try:
            return self.verify_target(account,room) and self.ui('check_empty').get('empty') is True
        except Exception:
            return False

    def prepared_matches(self,job,checkpoint):
        """Only a persisted, unchanged, proven-unsent draft is resumable."""
        if checkpoint.get('phase') not in ('quote_ready','paste','draft_ready','send_intent'):return False
        if checkpoint.get('quote_status')!='native_verified' or not checkpoint.get('editor_hash'):return False
        if not self.verify_target(job['account'],job['room']):return False
        if self.source_info(job).get('source_state')!='available':return False
        state=self.ui('check_input')
        if state.get('editor_hash')!=checkpoint['editor_hash']:return False
        if checkpoint['phase'] in ('quote_ready','paste'):
            return state.get('input_bytes')==0
        return state.get('input_sha256')==job['answer_hash']

    def resume_prepared(self,job,checkpoint):
        if not self.prepared_matches(job,checkpoint):
            raise DeliveryNotAttempted('Prepared draft changed',code='prepared_draft_changed')
        self.current_job=job;self.expected_editor=checkpoint['editor_hash']
        self.last_quote_status='native_verified'
        if checkpoint['phase'] in ('quote_ready','paste'):
            self.checkpoint('paste')
            try:state=self.ui('paste',text=job['answer'],expected_editor=self.expected_editor)
            except Exception as exc:
                raise DeliveryNotAttempted('Resume paste failed',code=getattr(exc,'code','adapter_preparation_error')) from exc
            if state.get('input_sha256')!=job['answer_hash']:
                raise DeliveryNotAttempted('Wrong draft',code='input_mismatch')
            self.expected_editor=state['editor_hash'];self.checkpoint('draft_ready')
        return self.send_existing_draft_and_verify(job['account'],job['room'],job['answer'])

    def source_info(self,job):
        request=dict(account=job['account'],group=job['room'],message_id=job['message_id'])
        run=subprocess.run(['wsl','-d','WeChatAgent','-u','root','--exec','python3','/opt/wechat-agent-v2/inbox.py','--quote-info'],
            input=json.dumps(request),capture_output=True,encoding='utf-8',timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        if run.returncode: raise RuntimeError('source_check_unavailable')
        result=json.loads(run.stdout)
        if not isinstance(result,dict) or not isinstance(result.get('unique'),bool):
            raise RuntimeError('invalid_source_check')
        return result

    def prepare_quote(self,job):
        # Locate the source in this authorized group; never silently send plain.
        # The model cannot choose a target message or click coordinates.
        compact=quote_text
        text=job['question']
        self.last_quote_status='source_invalid'
        if not text or len(text)>4000: return False
        request=dict(account=job['account'],group=job['room'],message_id=job['message_id'])
        self.last_quote_status='source_not_unique'
        source_info=self.source_info(job)
        if source_info.get('source_state')=='revoked':
            self.last_quote_status='source_revoked'
            raise SourceRevoked()
        latest_mode=not source_info.get('unique') and source_info.get('latest_match') is True
        if not source_info.get('unique') and not latest_mode:return False
        if latest_mode and not self.ui('quote_latest').get('at_bottom'):return False
        def candidates_for(lines,height):
            found=source_candidates(text,lines,height)
            return [max(found,key=lambda line:line['y'])] if latest_mode and found else found
        def view():
            capture=self.ui('quote_view'); im=Image.open(io.BytesIO(base64.b64decode(capture['image'])))
            return capture,asyncio.run(recognize(self.engine,im.resize((im.width*2,im.height*2))))
        cap,lines=view()
        candidates=candidates_for(lines,cap['height'])
        self.last_quote_status='source_not_visible'
        # A slow answer/new messages may have pushed the source above the view.
        # Bounded navigation only inside the already verified chat; every scroll
        # is guarded by the current frame, as are the subsequent source clicks.
        copied=False
        for page in range(7):
            if len(candidates)>1:return False
            if candidates:
                line=candidates[0]; point=dict(x=(line['x']+line['right'])/2,y=(line['y']+line['bottom'])/2)
                self.ui('quote_click',**point,right=True,expected_view=cap['view_hash'])
                menu_cap,menu=view(); copies=[line for line in menu if compact(line['text'])=='复制']
                if len(copies)==1:
                    copy=copies[0];self.ui('quote_click',x=(copy['x']+copy['right'])/2,y=(copy['y']+copy['bottom'])/2,expected_view=menu_cap['view_hash'])
                    self.last_quote_status='source_copy_mismatch'
                    if compact(self.ui('quote_copy_read')['text'])==compact(text):
                        copied=True;break
                    return False
                else:self.last_quote_status='copy_menu_unavailable'
                # A quoted preview can match OCR but is not the original bubble.
                # Dismiss its menu and keep searching; never authorize by OCR.
                self.ui('quote_escape');cap,lines=view()
            if page==6:break
            self.ui('quote_scroll',expected_view=cap['view_hash'])
            cap,lines=view();candidates=candidates_for(lines,cap['height'])
        if not copied:return False
        if latest_mode:
            recheck=subprocess.run(['wsl','-d','WeChatAgent','-u','root','--exec','python3','/opt/wechat-agent-v2/inbox.py','--quote-info'],
                input=json.dumps(request),capture_output=True,encoding='utf-8',timeout=20,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            if recheck.returncode or not json.loads(recheck.stdout).get('latest_match'):return False
        cap,lines=view()
        matches=candidates_for(lines,cap['height'])
        self.last_quote_status='source_moved'
        if len(matches)!=1: return False
        line=matches[0];self.ui('quote_click',x=(line['x']+line['right'])/2,y=(line['y']+line['bottom'])/2,right=True,expected_view=cap['view_hash'])
        menu_cap,menu=view(); quotes=[line for line in menu if compact(line['text'])=='引用']
        if len(quotes)!=1:
            self.last_quote_status='quote_menu_unavailable'
            self.ui('quote_escape');return False
        # Provenance is the unique DB source + exact clipboard readback + fresh
        # frame checked atomically before EVERY click, not OCR of a pale preview.
        quote=quotes[0];self.ui('quote_click',x=(quote['x']+quote['right'])/2,y=(quote['y']+quote['bottom'])/2,expected_view=menu_cap['view_hash'])
        # read_input changes focus/scrolling. Establish the quote fingerprint
        # using that same canonical read used by paste, not an earlier viewport.
        state=self.ui('check_input')
        if state.get('input_bytes')!=0:raise RuntimeError('Existing draft')
        if state.get('blank') or state.get('editor_hash')==self.expected_editor:
            raise RuntimeError('Quote was not created')
        self.expected_editor=state['editor_hash']
        self.last_quote_status='native_verified'
        return True

    def send_existing_draft_and_verify(self,account,room,text):
        # Recovery entry point is operator-only after independently checking
        # that an uncertain job has no receipt and its exact draft is present.
        digest=hashlib.sha256(text.encode()).hexdigest()
        try:
            if not self.verify_target(account,room): raise RuntimeError('Target changed')
            if self.ui('check_input').get('input_sha256')!=digest: raise RuntimeError('Draft changed')
        except Exception as exc:
            raise DeliveryNotAttempted('Draft verification failed') from exc
        since=time.time()-2
        for attempt in range(2):
            try:
                self.checkpoint('send_intent')
                self.ui('send',text_sha256=digest,expected_editor=self.expected_editor)
                self.checkpoint('await_receipt')
                break
            except DeliveryNotAttempted:
                # The adapter explicitly proves no Send click occurred. Recheck
                # both content and native quote before ONE local retry. Transport
                # loss or errors after clicking never enter this branch.
                if (attempt or not self.verify_target(account,room)
                    or self.ui('check_input').get('input_sha256')!=digest
                    or self.ui('quote_editor').get('editor_hash')!=self.expected_editor):
                    raise
        for _ in range(12):
            receipt=self.find_receipt(account,room,digest,since)
            if receipt: return receipt
            time.sleep(1)
        raise TimeoutError('No confirmed receipt; never automatically resend')

    def find_receipt(self,account,room,digest,since):
        request=dict(account=account,group=room,text_sha256=digest,since=since)
        run=subprocess.run(['wsl','-d','WeChatAgent','-u','root','--exec','python3',
            '/opt/wechat-agent-v2/inbox.py','--receipt'],input=json.dumps(request),
            capture_output=True,encoding='utf-8',timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        if run.returncode: raise RuntimeError('Receipt unavailable')
        return json.loads(run.stdout)
