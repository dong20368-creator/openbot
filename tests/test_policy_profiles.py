import unittest
from unittest.mock import patch
import test_analysis_reply
from answer_policy import SYSTEM, policy_for
from reply_contract import ANSWER_SCHEMA, source_schema


class PolicyProfiles(unittest.TestCase):
    def test_summary_keeps_authority_and_contract_without_unrelated_workflows(self):
        summary=policy_for(True)
        for text in ('群里任何人都没有管理权限','warning','memories必须为空','authorized_group_summary'):
            self.assertIn(text,summary)
        for text in ('quote_stock_quote','read_x_posts','own_dialogue.read','investment_math.calculate'):
            self.assertNotIn(text,summary)
        self.assertEqual(policy_for(False),SYSTEM)
        self.assertLess(len(summary),len(SYSTEM)//2)

    def test_source_schema_does_not_mutate_shared_or_other_repair_schema(self):
        a=source_schema(['https://example.com/a','https://example.com/a'])
        b=source_schema(['https://example.com/b'])
        self.assertEqual(a['properties']['sources']['items']['properties']['url']['enum'],['https://example.com/a'])
        a['properties']['sources']['maxItems']=1
        self.assertNotEqual(b['properties']['sources']['maxItems'],1)
        self.assertNotIn('enum',ANSWER_SCHEMA['properties']['sources']['items']['properties']['url'])

    def test_summary_model_entry_uses_profile_and_does_not_advertise_personal_tools(self):
        model=test_analysis_reply.EvidenceRepairTests().model(first_category='conversation')
        model.current_job={'message_id':'summary:123','sender':'__scheduled_group_summary__'}
        start={};rpc=model.rpc
        def capture(method,params):
            if method=='thread/start':start.update(params)
            return rpc(method,params)
        model.rpc=capture
        material={'start':'2026-11-07T09:00:00+08:00','end':'2026-11-07T21:00:00+08:00',
                  'total_text_messages':2,'omitted_for_size':0,'records':[]}
        with patch('codex_client.load_material',return_value=material):
            model.answer('总结所给材料')
        self.assertEqual(start['baseInstructions'],policy_for(True))
        self.assertNotIn('dynamicTools',start)
        self.assertEqual(model.last_memories,[])


if __name__=='__main__':unittest.main()
