import json
import unittest
from reply_contract import checked_reply,clean_provider_citations,public_model_info


class ReplyPresentationTests(unittest.TestCase):
    def reply(self,answer,**updates):
        return json.dumps({'category':'conversation','model_disclosure':False,'answer':answer,
            'sources':[],'memories':[],'unavailable_reason':'none',**updates})

    def test_citation_wrappers_preserve_all_claim_text(self):
        self.assertEqual(clean_provider_citations('前<cite index="10-2,10-4">第一句。第二句。</cite>后'),
            '前第一句。第二句。后')

    def test_literal_code_markup_is_preserved(self):
        sample='例子：`<cite index="1-2">内容</cite>`\n```html\n<cite index="3-4">代码</cite>\n```'
        self.assertEqual(clean_provider_citations(sample),sample)

    def test_cleaning_does_not_bypass_source_verification(self):
        raw=self.reply('<cite index="1-2">有待核实的结论</cite>',category='verified',
            sources=[{'url':'https://example.com/report','as_of':'2026-11-15'}])
        result=checked_reply(raw,[],'test')
        self.assertNotIn('有待核实的结论',result)
        events=[{'type':'webSearch','status':'completed','action':{'type':'openPage','url':'https://example.com/report'}}]
        self.assertEqual(checked_reply(raw,events,'test'),'有待核实的结论')

    def test_identity_model_id_and_spaces_are_exact(self):
        answer='模型：Gemini Argon（incorrect-id）\n推理档位：MEDIUM（中档）\n调用方式：通过 incorrect channel 调用。'
        raw=self.reply(answer,model_disclosure=True)
        self.assertEqual(checked_reply(raw,[],'test',model='gemini-4-argon-medium',effort='medium'),
            public_model_info('gemini-4-argon-medium','medium'))

    def test_mixed_answer_other_content_is_preserved(self):
        answer='模型：wrong\n推理档位：wrong\n调用方式：wrong\n另外，2+2=4。'
        result=checked_reply(self.reply(answer,model_disclosure=True),[],'test',model='gemini-4-argon-medium',effort='medium')
        self.assertEqual(result,public_model_info('gemini-4-argon-medium','medium')+'\n另外，2+2=4。')


if __name__=='__main__':unittest.main()
