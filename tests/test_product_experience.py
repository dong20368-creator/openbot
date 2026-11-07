import unittest
from status_view import present
from reply_contract import fit_reply
from answer_limits import MAX_REPLY_CHARS


class ProductExperience(unittest.TestCase):
    def status(self,state='running',jobs=None,**changes):
        return dict(state=state,updated=100,jobs=jobs or {},**changes)

    def test_counts_distinguish_waiting_generation_delivery_and_history(self):
        _,details,_=present(self.status(jobs={'pending':2,'generating':3,'sending':1,'failed':4,'expired':5}),now=101)
        for value in ('排队 2','正在回答 3','正在发送 1','累计未完成 4','累计已过期 5'):
            self.assertIn(value,details)

    def test_stale_status_never_claims_work_is_current(self):
        title,details,note=present(self.status(jobs={'generating':3}),now=200)
        self.assertIn('无法确认',title);self.assertIn('非实时',details)
        self.assertNotIn('正在生成',note)

    def test_stop_request_takes_precedence_over_inflight_work(self):
        title,_,note=present(self.status(jobs={'generating':1}),stopping=True,now=101)
        self.assertEqual(title,'正在暂停');self.assertIn('已开始',note)

    def test_old_stopped_state_is_not_proof_of_pause_without_stop_marker(self):
        self.assertIn('已过期',present(self.status('stopped'),now=200)[0])
        self.assertEqual(present(self.status('stopped'),stopping=True,now=200)[0],'已暂停')

    def test_uncertain_only_does_not_imply_whole_service_is_blocked(self):
        note=present(self.status('attention',{'uncertain':1}),now=101)[2]
        self.assertIn('其他新问题可继续',note)
        note=present(self.status('attention',{'attention':1,'uncertain':1}),now=101)[2]
        self.assertNotIn('其他新问题可继续',note)

    def test_degraded_does_not_claim_all_failures_are_network(self):
        self.assertIn('服务异常',present(self.status('degraded'),now=101)[2])
        self.assertIn('自动重连',present(self.status('degraded',intake={'reason':'native_reconnecting'}),now=101)[2])

    def test_long_answer_prefers_sentence_end_and_preserves_footer(self):
        text='开头。'+'说明内容。'*900
        result=fit_reply(text,'\n原帖：https://example.com/post')
        self.assertLessEqual(len(result),MAX_REPLY_CHARS)
        self.assertIn('。\n（内容较长',result)
        self.assertTrue(result.endswith('https://example.com/post'))

    def test_truncated_code_closes_fence_before_notice(self):
        result=fit_reply('示例\n```python\n'+'print(1)\n'*900+'```')
        self.assertEqual(sum(line.startswith('```') for line in result.splitlines())%2,0)
        self.assertIn('\n```\n（内容较长',result)
        self.assertLessEqual(len(result),MAX_REPLY_CHARS)

    def test_oversized_footer_cannot_exceed_transport_limit(self):
        with self.assertRaises(ValueError):fit_reply('answer','x'*(MAX_REPLY_CHARS+1))


if __name__=='__main__':unittest.main()
