import copy,json,unittest
from evidence import evidence_report,evidence_feedback,canonical_url
from futu_evidence import source_url


class FutuEvidenceTests(unittest.TestCase):
    def item(self):
        return {'type':'mcpToolCall','server':'futu_research','tool':'quote_stock_quote','status':'completed',
                'readOnlyHint':True,'result':{'content':[{'type':'text','text':json.dumps({'ret_code':0,
                'data':{'quote_list':[{'code':'HK.00700','last_price':100,'data_date':'2026-11-06'}]}})}]}}

    def test_successful_receipt_is_provenance_not_web_open(self):
        item=self.item();url='futu://quote_stock_quote'
        data={'category':'verified','sources':[{'url':url,'as_of':'2026-11-06'}]}
        self.assertEqual(source_url(item),url);self.assertTrue(evidence_report(data,[item])['passed'])
        self.assertFalse(evidence_report(data,[])['passed'])
        self.assertIn(url,json.loads(evidence_feedback(data,[item]))['本次可核对的实际访问网址'])

    def test_failed_empty_wrong_server_and_trade_never_prove_data(self):
        item=self.item()
        for mutation in ({'server':'other'},{'status':'failed'},{'tool':'trade_get_positions'},
                         {'readOnlyHint':False},{'error':{'message':'failed'}},{'result':{'isError':True}},
                         {'result':{'content':[{'type':'text','text':'invalid'}]}},
                         {'result':{'structuredContent':{'ret_code':0,'data':{'quote_list':[]}}}},
                         {'result':{'structuredContent':{'ret_code':-1,'data':{'quote_list':[1]}}}},
                         {'result':{'structuredContent':{'ret_code':False,'data':{'quote_list':[1]}}}}):
            with self.subTest(mutation=mutation):self.assertIsNone(source_url({**item,**mutation}))
        self.assertIsNone(canonical_url('futu://trade_get_positions'))

    def test_mixed_sources_still_require_actual_web_evidence(self):
        data={'category':'verified','sources':[{'url':'futu://quote_stock_quote','as_of':'today'},
                                              {'url':'https://example.com/report','as_of':'today'}]}
        self.assertFalse(evidence_report(data,[self.item()])['passed'])
        self.assertTrue(evidence_report(data,[self.item(),{'action':{'type':'openPage','url':'https://example.com/report'}}])['passed'])


if __name__=='__main__':unittest.main()
