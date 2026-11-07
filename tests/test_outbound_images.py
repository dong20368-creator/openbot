import base64
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
import outbound_images as public
import native_images as native
from evidence import evidence_report

URL='https://x.com/example/status/123456789'

class Images(unittest.TestCase):
    def setUp(self):
        b=io.BytesIO();Image.new('RGB',(16,16),'blue').save(b,'PNG');self.raw=b.getvalue()
        self.a={'sha256':hashlib.sha256(self.raw).hexdigest(),'bytes':len(self.raw),'mime':'image/png','post_url':URL}
        self.job={'id':1,'attachments':json.dumps([self.a])}
    def test_real_decode_and_exact_bytes(self):
        doc={'code':200,'tweet':{'id':'123456789','url':URL,'author':{'screen_name':'example','protected':False},
            'media':{'photos':[{'url':'https://pbs.twimg.com/media/valid.png?name=orig'}]}}}
        def fetch(url,limit):return json.dumps(doc).encode() if 'api.' in url else self.raw
        with tempfile.TemporaryDirectory() as tmp:
            r,previews=public.prepare(URL,Path(tmp),fetch)
            self.assertEqual(r['photos'][0]['sha256'],self.a['sha256'])
            self.assertTrue(previews[0].startswith('data:image/jpeg;base64,'))
            wire=public.wire(r['photos'],Path(tmp))
            self.assertEqual(native.validate_payload({'attachments':json.dumps(r['photos'])},wire),[self.raw])
            (Path(tmp)/(self.a['sha256']+'.bin')).write_bytes(b'changed')
            with self.assertRaises(ValueError):public.wire(r['photos'],Path(tmp))
        doc['tweet']['author']['screen_name']='wrong'
        with self.assertRaises(ValueError):public.prepare(URL,fetch=fetch)
    def test_block_arbitrary_targets_and_redirects(self):
        for url in ('http://127.0.0.1/x','https://pbs.twimg.com.evil/media/a.jpg?name=orig','https://pbs.twimg.com/profile_images/a.jpg','https://api.fxtwitter.com/example/status/123456789?private=1','file:///secret'):
            with self.assertRaises(ValueError):public.get(url,100)
        with self.assertRaises(ValueError):public.NoRedirect().redirect_request(None,None,None,None,None,None)
    def test_manifest_and_hash_validation(self):
        with self.assertRaises(ValueError):native.validate_payload(self.job,[])
        with self.assertRaises(ValueError):native.validate_payload(self.job,[{**self.a,'data':base64.b64encode(b'bad').decode()}])
        self.assertFalse(public.receipt_matches(self.job,{'images':[]}))
        self.assertTrue(public.receipt_matches(self.job,{'images':[{'sha256':self.a['sha256'],'message_id':'s:9'}]}))
    def test_image_source_requires_local_success(self):
        e={'type':'publicImageRead','success':True,'url':'https://api.fxtwitter.com/example/status/123456789','count':1}
        d={'category':'verified','sources':[{'url':e['url'],'as_of':'today'}]}
        self.assertTrue(evidence_report(d,[e])['passed'])
        self.assertFalse(evidence_report(d,[{**e,'success':False}])['passed'])
    def test_latest_ignores_other_authors_and_uses_newest_own_photo(self):
        def post(handle,stamp,ident):return {'author':{'screen_name':handle},'created_timestamp':stamp,'url':f'https://x.com/{handle}/status/{ident}', 'media':{'photos':[{}]}}
        doc={'code':200,'results':[post('other',500,'555555'),post('example',100,'111111'),post('example',200,'222222')]}
        with patch('outbound_images.prepare',return_value=({'photos':[]},[])) as prepare:
            public.latest('example',fetch=lambda *args:json.dumps(doc))
            self.assertEqual(prepare.call_args.args[0],'https://x.com/example/status/222222')
    def test_worker_persists_images_and_rejects_text_only_receipt(self):
        import test_core
        from core import Worker
        fixture=test_core.PipelineTests();fixture.setUp()
        try:
            fixture.add();model=test_core.Model();model.last_attachments=[self.a]
            Worker(fixture.store,model,test_core.Sender(),'send',stage='generate').run_one()
            row=dict(fixture.store.db.execute('SELECT * FROM jobs').fetchone())
            self.assertEqual(row['state'],'ready');self.assertEqual(json.loads(row['attachments']),[self.a])
            Worker(fixture.store,None,test_core.Sender(),'send',stage='deliver').run_one()
            self.assertEqual(fixture.store.status(),{'uncertain':1})
        finally:fixture.tearDown()
    def transport(self):
        class T:
            account='account';room='room';server_id='server';base='/v1/accounts/account'
            def __init__(s):s.db=sqlite3.connect(':memory:');s.sends=0
            def request(s,path,body=None,key=None):
                if body:s.sends+=1;return {}
                return {'accountId':s.account,'threadId':s.room,'kind':'send','sourceId':'upload1','state':'accepted',
                    'serverMessageId':'99','result':{'messageIds':['m1'],'profileProjection':'complete'}}
            def message(s,mid):return {'accountId':s.account,'threadId':s.room,'direction':'outgoing','type':3,'serverId':'99'}
        return T()
    def test_duplicate_and_recovery_never_resend(self):
        t=self.transport()
        with patch('native_images.upload',return_value='upload1') as upload:
            first=native.deliver(t,self.job,[self.raw],lambda:None)
            second=native.deliver(t,self.job,[self.raw],lambda:None)
            self.assertEqual(first,second);self.assertEqual(native.recover(t,self.job),first)
            self.assertEqual(t.sends,1);self.assertEqual(upload.call_count,1)
    def test_uncertain_upload_blocks_blind_repeat(self):
        t=self.transport()
        with patch('native_images.upload',side_effect=TimeoutError):
            with self.assertRaises(TimeoutError):native.deliver(t,self.job,[self.raw],lambda:None)
        with patch('native_images.upload') as upload:
            with self.assertRaises(RuntimeError):native.deliver(t,self.job,[self.raw],lambda:None)
            upload.assert_not_called()
        self.assertIsNone(native.recover(t,self.job));self.assertEqual(t.sends,0)
    def test_wrong_target_receipt_rejected(self):
        t=self.transport();t.message=lambda _: {'accountId':'other'}
        with patch('native_images.upload',return_value='upload1'):
            with self.assertRaises(ValueError):native.deliver(t,self.job,[self.raw],lambda:None)

if __name__=='__main__':unittest.main()
