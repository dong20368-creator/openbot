import unittest
from context_budget import bounded_history,estimated_tokens,excerpt_text

class CapabilityContext(unittest.TestCase):
    def turn(self,text,created=1):
        return dict(kind='current_person_history',continuity_role='latest_turn',question='请比较两种方案',answer=text,created=created)
    def test_last_options_of_long_recent_answer_are_complete(self):
        text='方案比较。'+'这是背景说明。'*400+'最后选项：甲是周一，乙是周三。'
        result=bounded_history('选乙', [self.turn(text)])
        self.assertEqual(result[0]['answer'],text)
        self.assertEqual(bounded_history('选乙',result),result)
    def test_large_record_keeps_tail_and_evidence_with_bounded_size(self):
        text='开头条件。'+'背景。'*6000+'最终纠正：预算改为4200，日期周四。'
        result=bounded_history('预算是多少',[self.turn(text)],budget=4000)
        self.assertTrue(result[0]['excerpt'])
        self.assertIn('预算改为4200',result[0]['answer'])
        self.assertLessEqual(estimated_tokens(result)+estimated_tokens({'本次问题':'预算是多少'})+1000,4000)
    def test_recent_turn_not_displaced_by_old_long_records(self):
        history=[dict(self.turn('旧讨论。'*5000,i),continuity_role='older_turn') for i in range(10)]
        history.append(self.turn('最新更正：用绿色。',20))
        result=bounded_history('那就按这个办',history,budget=4000)
        self.assertTrue(any('最新更正' in x.get('answer','') for x in result))
        self.assertLessEqual(estimated_tokens(result),4000)
    def test_source_input_is_not_mutated(self):
        x=self.turn('背景。'*6000+'结尾')
        original=dict(x);bounded_history('继续',[x],budget=4000)
        self.assertEqual(x,original)
    def test_excerpt_keeps_relevant_middle_without_hiding_omission(self):
        text='起始'+'甲'*2000+'关键条件为七天'+'乙'*2000+'末尾更正'
        result=excerpt_text(text,'关键条件',900)
        self.assertIn('关键条件为七天',result);self.assertIn('末尾更正',result)
        self.assertIn('[中间内容省略]',result);self.assertLessEqual(len(result),900)

if __name__=='__main__':unittest.main()
