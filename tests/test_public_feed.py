import json,queue,time,unittest
from unittest.mock import patch
from public_feed import latest,fetch,receipt,LATEST,BASE,source_url
from evidence import evidence_report
from codex_client import Codex

class PublicFeedTests(unittest.TestCase):
    def fixture(self,path):
        if path.startswith('/posts/'):return '<div>06:10 AM · November 7, 2026</div>'
        if path.startswith('/agents/latest'):
            return json.dumps({'entries':[{'id':'x-2118949294250348740','type':'post','date':'2026-11-07','source':'https://x.com/elonmusk/status/2118949294250348740'}]})
        return json.dumps({'id':'x-2118949294250348740','type':'post','date':'2026-11-07','url':'https://x.com/elonmusk/status/2118949294250348740','text':'@someone 💯'})

    def test_fresh_text_date_and_unknown_timezone_are_preserved(self):
        r=latest(self.fixture)
        self.assertEqual(r['posts'][0]['text'],'@someone 💯')
        self.assertIn('06:10 AM',r['posts'][0]['display_time'])
        self.assertEqual(r['timezone'],'not specified by archive')
        d={'category':'verified','sources':[{'url':LATEST,'as_of':'2026-11-07'}]}
        self.assertTrue(evidence_report(d,[receipt(r)])['passed'])
        self.assertFalse(evidence_report(d,[])['passed'])

    def test_changed_provider_or_empty_data_cannot_be_evidence(self):
        for d in ({'entries':[]},{'entries':[{'id':'x-2118949294250348740','type':'post','date':'2026-11-07','source':'https://x.com/other/status/2118949294250348740'}]}):
            with self.assertRaises(ValueError):latest(lambda p:json.dumps(d))
        for mutation in ({'success':False},{'count':0},{'count':True},{'url':'https://example.com'},{'type':'search'}):
            self.assertIsNone(source_url({**receipt({'posts':[1]}),**mutation}))

    def test_no_arbitrary_network_target_or_redirect(self):
        for path in ('http://localhost','/agents/search?q=private','/posts/../../secret'):
            with self.assertRaises(ValueError):fetch(path)

    def model(self):
        c=Codex();c.events=queue.Queue();c.deadline=time.monotonic()+30;c.active_thread='a';c.public_feed_events=[]
        c.sent=[];c.send=c.sent.append
        return c

    def call(self,c,thread='a',args=None):
        c.events.put({'id':1,'method':'item/tool/call','params':{'threadId':thread,'namespace':'public_feed','tool':'latest_elon_posts','arguments':{} if args is None else args}})
        c.receive()

    def test_tool_scoped_budget_and_failed_fetch_no_receipt(self):
        c=self.model()
        with patch('codex_client.latest_public_posts',return_value=latest(self.fixture)) as read:
            self.call(c,'other');self.call(c,args={'url':'http://localhost'})
            self.assertEqual(read.call_count,0)
            self.call(c);self.call(c)
            self.assertEqual(read.call_count,1);self.assertEqual(len(c.public_feed_events),1)
            self.assertFalse(c.sent[-1]['result']['success'])
        c=self.model()
        with patch('codex_client.latest_public_posts',side_effect=TimeoutError('private diagnostic')):
            self.call(c)
        self.assertFalse(c.sent[-1]['result']['success']);self.assertEqual(c.public_feed_events,[])
        self.assertNotIn('private diagnostic',json.dumps(c.sent))

    def test_low_remaining_budget_uses_fallback_without_fetch(self):
        c=self.model();c.deadline=time.monotonic()+2
        with patch('codex_client.latest_public_posts') as read:self.call(c);read.assert_not_called()

if __name__=='__main__':unittest.main()
