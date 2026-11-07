"""Native delivery fault matrix with a real temporary journal and fake hub."""
import tempfile
from pathlib import Path
import unittest
from protocol_transport import ProtocolTransport,TransportError,NoRedirect


class ProtocolFaults(unittest.TestCase):
    def test_lost_response_restart_and_changed_payload_never_resubmit(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent/'runtime') as tmp:
            journal=Path(tmp)/'intents.db';posts=[];visible=[False]
            job={'id':'1','account':'native','room':'room','source_id':'source','source_server_id':'12','sender':'alice','answer':'answer'}
            def request(path,body=None,key=None):
                if path=='/v1/status':return {'serverId':'hub'}
                if body is not None:
                    posts.append(body);raise TransportError('transport_unavailable')
                if not visible[0]:raise TransportError('http_404')
                return {'id':'intent','accountId':'native','destination':'room','state':'accepted','localMessageId':'out','serverId':'13'}
            def message(mid):
                if mid=='source':return {'id':mid,'accountId':'native','threadId':'room','serverId':'12','senderId':'alice'}
                return {'id':'out','accountId':'native','threadId':'room','serverId':'13','direction':'outgoing','body':'answer','type':49,'appMessageType':57,'quote':{'serverId':'12'}}
            def client():
                t=ProtocolTransport('http://127.0.0.1:6175','synthetic','hub','native','room',journal)
                t.request=request;t.message=message;return t
            t=client()
            try:self.assertEqual(t.deliver(job,timeout=0)['state'],'needs_review')
            finally:t.close()
            t=client();visible[0]=True
            try:
                self.assertEqual(t.deliver(job,timeout=0)['state'],'hub_verified')
                self.assertEqual(len(posts),1)
                with self.assertRaisesRegex(TransportError,'job_changed'):t.deliver({**job,'answer':'changed'},timeout=0)
                with self.assertRaises(TransportError):t.deliver({**job,'room':'other'},timeout=0)
                self.assertEqual(len(posts),1)
            finally:t.close()

    def test_wrong_hub_source_or_receipt_cannot_confirm(self):
        for fault in ('hub','source','recall','receipt'):
            with self.subTest(fault=fault):
                t=ProtocolTransport('http://127.0.0.1:6175','synthetic','hub','native','room',':memory:');posts=[]
                job={'id':'1','account':'native','room':'room','source_id':'source','source_server_id':'12','sender':'alice','answer':'answer'}
                def request(path,body=None,key=None):
                    if path=='/v1/status':return {'serverId':'other' if fault=='hub' else 'hub'}
                    if body is not None:posts.append(body);return {}
                    return {'id':'intent','accountId':'native','destination':'room','state':'accepted','localMessageId':'out','serverId':'13'}
                def message(mid):
                    if mid=='source':return {'id':mid,'accountId':'native','threadId':'other' if fault=='source' else 'room','serverId':'12','senderId':'alice','recall':fault=='recall'}
                    return {'id':'out','accountId':'native','threadId':'room','serverId':'13','direction':'outgoing','body':'answer','type':49,'appMessageType':57,'quote':{'serverId':'WRONG'}}
                t.request=request;t.message=message
                try:
                    with self.assertRaises(TransportError):t.deliver(job,timeout=0)
                    self.assertEqual(len(posts),1 if fault=='receipt' else 0)
                finally:t.close()

    def test_token_cannot_be_redirected_to_nonlocal_endpoint(self):
        for endpoint in ('https://example.com','http://127.0.0.1:6175/path','http://user@127.0.0.1:6175','http://127.0.0.1:6175?x=1'):
            with self.subTest(endpoint=endpoint),self.assertRaises(TransportError):
                ProtocolTransport(endpoint,'synthetic','hub','native','room',':memory:')
        with self.assertRaisesRegex(TransportError,'redirect_refused'):NoRedirect().redirect_request(None,None,None,None,None,None)


if __name__=='__main__':unittest.main()
