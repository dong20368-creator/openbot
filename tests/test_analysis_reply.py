import json,unittest
from codex_client import Codex,checked_reply,compact_answer,has_verified_evidence,evidence_feedback
from evidence import evidence_report

class EvidenceRepairTests(unittest.TestCase):
    def model(self,repair_succeeds=True,timeout=120,first_category='verified',event_count=1,visited_first=False,grounding_succeeds=False,read_new=False):
        class Synthetic(Codex):
            def _start(self):self.backlog=[];self.turns=0;self.schemas=[];self.inputs=[]
            def close(self):pass
            def rpc(self,method,params):
                if method=='account/read':return {'account':{'type':'chatgpt'}}
                if method=='thread/start':return {'thread':{'id':'thread'}}
                if method=='turn/start':
                    self.schemas.append(params.get('outputSchema'))
                    self.inputs.append(params.get('input'))
                    self.turns+=1;turn=str(self.turns)
                    def item(value):return {'method':'item/completed','params':{'threadId':'thread','turnId':turn,'item':value}}
                    if self.turns==1:
                        for _ in range(event_count):self.backlog.append(item({'type':'webSearch','action':{'type':'openPage','url':'https://example.com/report'}}))
                    url='https://example.com/report' if (self.turns>1 and repair_succeeds) or visited_first or (self.turns==3 and grounding_succeeds) else 'https://example.com/not-opened'
                    if read_new and self.turns==2:
                        url='https://example.com/not-opened'
                        self.backlog.append(item({'type':'webSearch','action':{'type':'openPage','url':url}}))
                    data=dict(category='verified',answer='假设收入增长10%，这是情景估算。',sources=[dict(url=url,as_of='2026-06-30')],memories=[])
                    if self.turns==1:data['category']=first_category
                    if 'answer_edits' in (params.get('outputSchema') or {}).get('properties',{}):
                        data.pop('answer');data.pop('memories');data.update(answer_edits=[],model_disclosure=False,unavailable_reason='none')
                    self.backlog.append(item({'type':'agentMessage','id':'answer','phase':'final_answer','text':json.dumps(data)}))
                    self.backlog.append({'method':'turn/completed','params':{'threadId':'thread','turn':{'id':turn,'status':'completed'}}})
                    return {'turn':{'id':turn}}
                raise AssertionError(method)
        return Synthetic(timeout=timeout)
    def test_one_correction_turn_preserves_verified_analysis(self):
        m=self.model();reply=m.answer('分析公司')
        self.assertEqual(m.turns,2);self.assertIn('情景估算',reply)
        self.assertTrue(m.last_audit['evidence_passed']);self.assertTrue(m.last_audit['evidence_repair_attempted'])
        self.assertNotIn('https://',reply)
        self.assertEqual(set(m.schemas[1]['properties']['sources']['items']['properties']['url']['enum']),{'https://example.com/report','https://example.com/not-opened'})
        self.assertNotIn('enum',m.schemas[0]['properties']['sources']['items']['properties']['url'])

    def test_new_source_can_be_read_and_kept_during_repair(self):
        m=self.model(read_new=True);m.answer('最近五天动态')
        self.assertEqual(m.turns,2);self.assertTrue(m.last_audit['evidence_passed'])
        self.assertEqual(m.last_audit['observed_urls'],2)
        self.assertIn('https://example.com/not-opened',m.inputs[1][0]['text'])

    def test_limited_budget_grounds_without_expanding_search(self):
        m=self.model(timeout=30);m.answer('分析公司')
        self.assertEqual(m.turns,2)
        self.assertEqual(m.schemas[1]['properties']['sources']['items']['properties']['url']['enum'],['https://example.com/report'])
    def test_repair_never_loops_or_exposes_unvisited_claims(self):
        m=self.model(False);reply=m.answer('分析公司')
        self.assertEqual(m.turns,3);self.assertNotIn('收入增长',reply)
        self.assertFalse(m.last_audit['evidence_passed'])
    def test_short_remaining_budget_skips_extra_turn(self):
        m=self.model(timeout=10);m.answer('分析公司')
        self.assertEqual(m.turns,1);self.assertFalse(m.last_audit['evidence_repair_attempted'])

    def test_final_grounding_restricts_sources_and_retains_supported_answer(self):
        m=self.model(False,grounding_succeeds=True);reply=m.answer('比较公司')
        self.assertEqual(m.turns,3);self.assertIn('情景估算',reply)
        self.assertTrue(m.last_audit['evidence_grounding_attempted']);self.assertTrue(m.last_audit['evidence_passed'])
        self.assertEqual(m.schemas[-1]['properties']['sources']['items']['properties']['url']['enum'],['https://example.com/report'])

    def test_unavailable_is_reviewed_once_before_template_fallback(self):
        m=self.model(first_category='unavailable');reply=m.answer('分析公司')
        self.assertEqual(m.turns,2);self.assertIn('情景估算',reply)
        self.assertTrue(m.last_audit['evidence_passed'])

    def test_thirteen_tool_events_do_not_discard_verified_answer(self):
        m=self.model(event_count=13,visited_first=True);reply=m.answer('多方面分析')
        self.assertEqual(m.turns,1);self.assertIn('情景估算',reply)
        self.assertEqual(m.last_audit['web_events'],13)

    def test_privacy_warning_never_enters_answer_review(self):
        m=self.model(first_category='warning');reply=m.answer('synthetic privacy request')
        self.assertEqual(m.turns,1);self.assertIn('警告',reply)
        self.assertNotIn('情景估算',reply)

class CompactReplyTests(unittest.TestCase):
    def test_evidence_report_distinguishes_missing_time_unopened_and_invalid_urls(self):
        data={'category':'verified','sources':[{'url':'https://example.com/missing','as_of':'today'},
            {'url':'http://localhost/private','as_of':'today'},{'url':'https://example.com/open','as_of':' '}]}
        events=[{'action':{'type':'openPage','url':'https://example.com/open'}}]
        r=evidence_report(data,events)
        self.assertFalse(r['passed']);self.assertEqual(r['invalid_count'],1)
        self.assertEqual(r['undated_count'],1);self.assertEqual(r['unmatched_count'],2)
        self.assertNotIn('https://',json.dumps(r));self.assertNotIn('private',json.dumps(r))

    def test_correction_gets_exact_opened_urls_without_treating_search_or_failure_as_evidence(self):
        events=[{'action':{'type':'openPage','url':'https://example.com/actual'}},
            {'status':'failed','action':{'type':'openPage','url':'https://example.com/fail'}},
            {'action':{'type':'search','url':'https://example.com/search-only'}}]
        data={'sources':[{'url':'https://example.com/missing','as_of':'today'}]}
        feedback=json.loads(evidence_feedback(data,events))
        self.assertEqual(feedback['本次可核对的实际访问网址'],['https://example.com/actual'])
        self.assertEqual(feedback['尚未匹配的网址'],['https://example.com/missing'])

    def test_hiding_sources_never_disables_validation(self):
        data=dict(category='verified',answer='第一点\n\n  第二点    内容',sources=[dict(url='https://example.com/report',as_of='today')])
        events=[{'action':{'type':'openPage','url':'https://example.com/report'}}]
        reply=checked_reply(json.dumps(data),events,'now')
        self.assertEqual(reply,'第一点\n第二点 内容');self.assertTrue(has_verified_evidence(data,events))
        self.assertNotIn('第一点',checked_reply(json.dumps(data),[],'now'))
    def test_code_indentation_remains_meaningful(self):
        self.assertEqual(compact_answer('说明\n\n```python\nif True:\n    print(1)\n```\n\n结束'),'说明\n```python\nif True:\n    print(1)\n```\n结束')

if __name__=='__main__':unittest.main()
