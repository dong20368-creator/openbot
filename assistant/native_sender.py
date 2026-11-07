"""Native protocol delivery through the local WSL bridge; no GUI fallback."""
import json
import os
import subprocess
import time
from core import SourceRevoked,NativeDeliveryRejected


class NativeDeliveryUnknown(RuntimeError):
    def __init__(self,code):
        super().__init__(code)
        self.code=code
from outbound_images import wire


class NativeSender:
    native_quotes_enabled=True
    independent_intents=True

    def __init__(self,config):
        self.config=config
        self.current_job=None
        self.checkpoint_sink=None

    def call(self,op,**data):
        run=subprocess.run(['wsl','-d','WeChatAgent','-u','root','--exec','python3',
            '/opt/wechat-agent-v2/native_gateway.py'],input=json.dumps({'op':op,**data}),
            capture_output=True,encoding='utf-8',timeout=115,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        result=json.loads(run.stdout)
        if run.returncode:
            if result.get('error')=='source_revoked':raise SourceRevoked()
            if result.get('error') in ('native_transport_before_submission','native_service_rejected','native_send_admission_revoked','native_quote_target_unavailable'):
                raise NativeDeliveryRejected(result['error'])
            raise NativeDeliveryUnknown(result.get('error') if result.get('error') in ('native_delivery_uncertain','native_offline','native_gateway_failed') else 'native_delivery_not_confirmed')
        return result

    def verify_target(self,account,room):
        return ((account,room)==(self.config['account'],self.config['group'])
                and self.call('verify').get('verified') is True)

    ensure_target=verify_target

    def source_info(self,job):
        return self.call('source',job=dict(job))

    def send_and_verify(self,account,room,text):
        if (account,room)!=(self.config['account'],self.config['group']) or not self.current_job:
            raise RuntimeError('native_scope_mismatch')
        started=time.monotonic()
        receipt=self.call('send',job=self.current_job,text=text,images=wire(self.current_job.get('attachments')))
        self.last_timings={'native_send_and_readback_seconds':round(time.monotonic()-started,3)}
        return receipt

    def find_receipt(self,account,room,digest,since):
        # Body/time matching cannot identify a native send intent safely.
        return None

    def find_job_receipt(self,job):
        return self.call('receipt',job=dict(job))
