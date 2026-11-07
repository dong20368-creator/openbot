"""Cross-module regressions: actual model boundary, no network or group send."""
import json
import unittest
from unittest.mock import patch
from answer_limits import MAX_SOURCES, MAX_MEMORIES, MAX_REPLY_CHARS
from reply_contract import ANSWER_SCHEMA, checked_reply, fit_reply, material_status
from evidence import evidence_report
import test_analysis_reply
import test_public_research


class FlowContract(unittest.TestCase):
    def missing_material_answer(self, media, reference_id='s:synthetic'):
        model=test_analysis_reply.EvidenceRepairTests().model(timeout=10)
        model.current_job={'reference_id':reference_id,'message_id':'s:question'}
        model.media_provider=lambda _:media
        original=model.rpc
        def rpc(method, params):
            result=original(method,params)
            if method=='turn/start':
                for event in model.backlog:
                    item=event.get('params',{}).get('item',{})
                    if item.get('type')=='agentMessage':
                        item['text']=json.dumps({'category':'unavailable',
                            'unavailable_reason':'missing_material','answer':'UNTRUSTED_CANDIDATE',
                            'sources':[],'memories':[]})
            return result
        model.rpc=rpc
        return model.answer('分析本次引用材料')

    def test_document_text_without_images_is_available_through_model_boundary(self):
        media={'inputs':[],'notes':[{'materials':[{'document':'合成文档正文','notice':'摘录'}]}]}
        reply=self.missing_material_answer(media)
        self.assertIn('已取得',reply)
        self.assertNotIn('未能取得',reply)
        self.assertNotIn('原图',reply)

    def test_text_reference_does_not_claim_missing_photo(self):
        reply=self.missing_material_answer({'inputs':[],'notes':[]})
        self.assertIn('先前聊天记录',reply)
        self.assertNotIn('图片',reply)

    def test_attachment_failure_is_not_successful_content(self):
        media={'inputs':[],'notes':[{'unavailable':True,'notice':'未能取得材料'}]}
        reply=self.missing_material_answer(media)
        self.assertIn('未能取得',reply)
        self.assertNotIn('UNTRUSTED_CANDIDATE',reply)

    def test_document_whitespace_and_notice_do_not_count_as_content(self):
        self.assertFalse(material_status({'notes':[{'materials':[{'document':'  ','notice':'PDF'}]}]})['media_available'])
        self.assertFalse(material_status({'notes':[{'unavailable':True,'document':'invalid'}]})['media_available'])

    def test_unknown_category_cannot_render_candidate(self):
        for value in ('future_type',None):
            with self.assertRaises(ValueError):
                checked_reply(json.dumps({'category':value,'answer':'UNTRUSTED'}),[],'now')

    def test_source_limit_matches_schema_and_actual_evidence_validation(self):
        sources=[{'url':f'https://example.com/{i}','as_of':'2026-11-07'} for i in range(MAX_SOURCES+1)]
        events=[{'action':{'type':'openPage','url':s['url']}} for s in sources]
        self.assertEqual(ANSWER_SCHEMA['properties']['sources']['maxItems'],MAX_SOURCES)
        self.assertEqual(ANSWER_SCHEMA['properties']['memories']['maxItems'],MAX_MEMORIES)
        self.assertTrue(evidence_report({'category':'verified','sources':sources[:-1]},events)['passed'])
        self.assertIn('source_count',evidence_report({'category':'verified','sources':sources},events)['reasons'])

    def test_long_reply_reserves_footer_inside_hard_limit(self):
        footer='\n原帖：https://example.com/post'
        text=fit_reply('内容'*MAX_REPLY_CHARS,footer)
        self.assertLessEqual(len(text),MAX_REPLY_CHARS)
        self.assertTrue(text.endswith(footer))

    def test_research_budget_is_shared_and_cache_does_not_consume_it(self):
        model=test_public_research.ResearchTests().model()
        def call(tool,args):
            model.events.put({'id':1,'method':'item/tool/call','params':{
                'threadId':'a','namespace':'public_research','tool':tool,'arguments':args}})
            model.receive()
        with patch('codex_client.run_public_research',return_value={'reads':[]}) as read:
            call('latest_x_posts',{'handle':'synthetic'})
            call('latest_x_posts',{'handle':'synthetic'})
            self.assertEqual(read.call_count,1)
            call('read_x_posts',{'post_urls':['https://x.com/synthetic/status/123456']})
            call('futu_news',{'keyword':'Synthetic','news_type':1})
            self.assertEqual(read.call_count,2)
            self.assertFalse(model.sent[-1]['result']['success'])


if __name__=='__main__':unittest.main()
