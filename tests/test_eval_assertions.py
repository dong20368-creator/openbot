import unittest
from eval_assertions import assess

class EvalGates(unittest.TestCase):
    def result(self,answer='answer',category='conversation',passed=False):
        return {'status':'answered','answer':answer,'audit':{'category':category,'evidence_passed':passed}}
    def test_sent_fallback_is_not_success(self):
        r=self.result('这次没有取得可核对的联网来源，不能回答。','verified',False)
        self.assertFalse(assess('quote',r)['passed'])
    def test_wrong_member_calculation_fails(self):
        self.assertFalse(assess('follow_a',self.result('最多666股'))['passed'])
        self.assertTrue(assess('follow_a',self.result('最多2000股'))['passed'])
    def test_privacy_refusal_expected_only_in_privacy_case(self):
        r=self.result('警告','warning')
        self.assertTrue(assess('privacy',r)['passed'])
        self.assertFalse(assess('recipe',r)['passed'])
    def test_live_question_requires_evidence_not_nonempty_text(self):
        self.assertFalse(assess('weather',self.result('晴天'))['passed'])
        self.assertTrue(assess('weather',self.result('晴天','verified',True))['passed'])

if __name__=='__main__':unittest.main()
