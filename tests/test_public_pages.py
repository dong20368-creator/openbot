import io,json,unittest
from email.message import Message
from unittest.mock import patch
import public_pages as pages
from evidence import evidence_report
import test_analysis_reply

URL='https://www.frontiermodelforum.org/report'

class PublicPages(unittest.TestCase):
    def test_scope_rejects_private_redirect_query_and_pdf(self):
        for url in ('http://www.nhs.uk/a','https://127.0.0.1/a','https://www.nhs.uk.evil/a','https://user@www.nhs.uk/a',
            'https://www.nhs.uk:443/a','https://www.nhs.uk/a?private=x','https://www.nhs.uk/a.pdf','https://example.com/a'):
            self.assertFalse(pages.allowed(url))
        with self.assertRaises(ValueError):pages.NoRedirect().redirect_request(None,None,None,None,None,None)
    def test_real_parser_strips_scripts_and_only_success_is_evidence(self):
        raw=('<html><script>hidden secret</script><nav>navigation</nav><main>'+('verified body '*50)+'</main></html>').encode()
        response=io.BytesIO(raw);response.headers=Message();response.headers['Content-Type']='text/html'
        with patch('public_pages.urllib.request.build_opener') as build:
            build.return_value.open.return_value=response
            p=pages.read(URL)
        self.assertNotIn('hidden',p['text']);self.assertNotIn('navigation',p['text'])
        e=pages.receipt(p);d={'category':'verified','sources':[{'url':URL,'as_of':'today'}]}
        self.assertTrue(evidence_report(d,[e])['passed'])
        self.assertFalse(evidence_report(d,[{**e,'success':False}])['passed'])
        self.assertFalse(evidence_report(d,[{**e,'sha256':'bad'}])['passed'])
    def test_failures_and_unsupported_urls_not_added(self):
        seen=[]
        def fail(u):seen.append(u);raise TimeoutError('private diagnostic')
        self.assertEqual(pages.read_many([URL,URL,'https://localhost/private'],fail),[])
        self.assertEqual(seen,[URL])
    def test_fetched_content_is_given_to_model_and_removes_extra_repair(self):
        m=test_analysis_reply.EvidenceRepairTests().model(False)
        original=m.rpc
        def rpc(method,params):
            result=original(method,params)
            m.backlog=json.loads(json.dumps(m.backlog).replace('https://example.com/not-opened',URL))
            return result
        m.rpc=rpc
        page={'url':URL,'text':'Synthetic primary source evidence','sha256':'a'*64,'truncated':False}
        with patch('codex_client.read_primary_pages',return_value=[page]):reply=m.answer('公开合成题')
        self.assertEqual(m.turns,2);self.assertTrue(m.last_audit['evidence_passed'])
        self.assertEqual(m.last_audit['primary_page_reads'],1)
        self.assertIn(page['text'],m.inputs[1][0]['text'])
        self.assertFalse(m.last_audit['evidence_grounding_attempted'])

if __name__=='__main__':unittest.main()
