import json,unittest
from answer_revision import apply_revision,revision_schema
from reply_contract import ANSWER_SCHEMA
import test_analysis_reply


class AnswerRevision(unittest.TestCase):
    def revision(self,edits):
        return dict(category='conversation',sources=[],model_disclosure=False,
                    unavailable_reason='none',answer_edits=edits)

    def test_exact_edits_preserve_unaffected_text_and_do_not_mutate(self):
        original={'answer':'第一句不变。第二句有误。第三句保留。','memories':[]}
        r=apply_revision(original,self.revision([{'old':'第二句有误。','new':'第二句已更正。'}]))
        self.assertEqual(r['answer'],'第一句不变。第二句已更正。第三句保留。')
        self.assertIn('有误',original['answer'])

    def test_ambiguous_missing_and_overlapping_edits_rejected(self):
        for text,edits in [('aaa',[{'old':'a','new':'b'}]),('abc',[{'old':'x','new':'b'}]),
                           ('abcd',[{'old':'abc','new':'z'},{'old':'bc','new':'y'}])]:
            with self.assertRaises(ValueError):apply_revision({'answer':text},self.revision(edits))

    def test_schema_is_independent_and_does_not_request_full_prose(self):
        s=revision_schema(ANSWER_SCHEMA)
        self.assertNotIn('answer',s['properties']);self.assertNotIn('memories',s['properties'])
        self.assertIn('answer',ANSWER_SCHEMA['properties'])

    def test_invalid_patch_falls_back_to_grounding_not_silent_acceptance(self):
        m=test_analysis_reply.EvidenceRepairTests().model(grounding_succeeds=True,read_new=True)
        rpc=m.rpc
        def changed(method,params):
            result=rpc(method,params)
            if method=='turn/start' and m.turns==2:
                for event in m.backlog:
                    item=event.get('params',{}).get('item',{})
                    if item.get('type')=='agentMessage':
                        data=json.loads(item['text']);data['answer_edits']=[{'old':'不存在的片段','new':'x'}]
                        item['text']=json.dumps(data)
            return result
        m.rpc=changed;m.answer('合成问题')
        self.assertTrue(m.last_audit['revision_failed'])
        self.assertTrue(m.last_audit['evidence_grounding_attempted'])
        self.assertTrue(m.last_audit['evidence_passed'])
        self.assertEqual(set(m.last_audit['phase_seconds']),{'initial','repair','grounding'})
        self.assertIn('unopened_source',m.last_audit['repair_reasons'])


if __name__=='__main__':unittest.main()
