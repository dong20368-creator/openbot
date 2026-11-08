import json,unittest
from unittest.mock import patch
from evidence import citation_report
from reply_contract import checked_reply
import test_analysis_reply

class ConversationCitations(unittest.TestCase):
    def data(self,url='https://example.com/other'):
        return dict(category='conversation',answer='解释见[文档]('+url+')',sources=[dict(url=url,as_of='未注明日期')])
    def events(self):return [{'type':'webSearch','action':{'type':'openPage','url':'https://example.com/doc'}}]
    def test_stable_citation_cannot_bypass_validation(self):
        data=self.data();report=citation_report(data,self.events())
        self.assertTrue(report['required']);self.assertFalse(report['passed'])
        self.assertNotIn('example.com/other',checked_reply(json.dumps(data),self.events(),'today'))
    def test_read_stable_citation_and_plain_knowledge_work(self):
        data=self.data('https://example.com/doc')
        self.assertTrue(citation_report(data,self.events())['passed'])
        self.assertEqual(checked_reply(json.dumps(data),self.events(),'today'),data['answer'])
        self.assertTrue(citation_report(dict(category='conversation',answer='2+2=4',sources=[]),[])['passed'])
    def test_wrong_body_link_cannot_hide_behind_valid_sources(self):
        data=self.data('https://example.com/doc');data['answer']='见[文档](https://example.com/wrong)'
        self.assertFalse(citation_report(data,self.events())['passed'])
        data['sources']=[]
        self.assertFalse(citation_report(data,self.events())['passed'])
    def test_literal_code_link_is_not_treated_as_citation(self):
        data=dict(category='conversation',answer='代码示例：```md\n[x](https://example.com/x)\n```',sources=[])
        self.assertFalse(citation_report(data,[])['required'])
    def test_successful_mirror_can_link_its_recorded_original_only(self):
        original='https://x.com/Example/status/123456789'
        event={'type':'publicImageRead','success':True,'url':'https://api.fxtwitter.com/Example/status/123456789',
               'count':1,'original_urls':[original]}
        data=dict(category='conversation',answer='[原帖]('+original+')',sources=[])
        self.assertTrue(citation_report(data,[event])['passed'])
        self.assertFalse(citation_report(data,[{**event,'success':False}])['passed'])
        self.assertFalse(citation_report(data,[{**event,'original_urls':[]}])['passed'])
    def test_stable_unmatched_source_enters_existing_repair(self):
        m=test_analysis_reply.EvidenceRepairTests().model(first_category='conversation')
        with patch('codex_client.read_primary_pages',return_value=[]):answer=m.answer('查文档')
        self.assertEqual(m.turns,2);self.assertTrue(m.last_audit['citation_check']['passed'])
        self.assertIn('情景估算',answer)

if __name__=='__main__':unittest.main()
