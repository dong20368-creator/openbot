import copy
import json
import queue
import time
import unittest
from unittest.mock import patch
from public_research import futu_news, latest_x_posts, valid_url, receipt, source_url, run, NoRedirect
from evidence import evidence_report
from codex_client import Codex
from public_research import read_x_posts


class ResearchTests(unittest.TestCase):
    def test_post_batch_partial_failure_quote_and_source_identity(self):
        row=self.row();row['quote']=self.row('2118674834632978757',handle='other')
        def fetch(url):
            if '/bad/' in url:raise TimeoutError('secret')
            return {'code':200,'tweet':row}
        result=read_x_posts([row['url'],'https://x.com/bad/status/2118674834632978758'],fetch)
        self.assertEqual(len(result['reads']),1);self.assertEqual(len(result['failed']),1)
        self.assertEqual(result['reads'][0]['items'][0]['quoted_post']['author'],'other')
        self.assertNotIn('secret',json.dumps(result))
        event=receipt(result['reads'][0]);self.assertEqual(source_url(event),result['reads'][0]['source_url'])
        self.assertFalse(evidence_report({'category':'verified','sources':[{'url':result['failed'][0]['post_url'],'as_of':'today'}]},[event])['passed'])
        with self.assertRaises(ValueError):read_x_posts(['https://x.com/wrong/status/'+row['id']],fetch)

    def test_post_batch_rejects_arbitrary_urls_and_all_failure(self):
        for urls in ([],['http://localhost'],['https://x.com/i/trending/12345'],
                     ['https://x.com/Qwen/status/123456?url=http://localhost'],
                     ['https://x.com/Qwen/status/123456']*4):
            with self.assertRaises(ValueError):read_x_posts(urls)
        with self.assertRaises(ValueError):read_x_posts([self.row()['url']],lambda u:{'code':404})

    def test_batch_handler_records_only_successful_individual_reads(self):
        row=self.row();r=read_x_posts([row['url']],lambda u:{'code':200,'tweet':row})
        c=self.model()
        with patch('codex_client.run_public_research',return_value=r):
            c.events.put({'id':1,'method':'item/tool/call','params':{'threadId':'a',
                'namespace':'public_research','tool':'read_x_posts','arguments':{'post_urls':[row['url']]}}})
            c.receive()
        self.assertTrue(c.sent[-1]['result']['success'])
        self.assertEqual(len(c.public_feed_events),1)
        self.assertEqual(source_url(c.public_feed_events[0]),r['reads'][0]['source_url'])

    def row(self, ident='2118674834632978755', ts=None, handle='Alibaba_Qwen'):
        return {'type':'status','id':ident,'url':f'https://x.com/{handle}/status/{ident}',
                'text':'New public statement','created_timestamp':ts or int(time.time())-10,
                'author':{'screen_name':handle,'protected':False}}

    def news(self):
        return {'code':0,'data':[{'title':'Public news','publish_time':str(int(time.time())-10),
                                 'url':'https://news.futunn.com/post/123?src=39'}]}

    def test_headline_receipt_does_not_credit_unread_article(self):
        r=futu_news('Tencent',fetcher=lambda u:self.news())
        self.assertFalse(r['items'][0]['full_text_read'])
        self.assertTrue(evidence_report({'category':'verified','sources':[{'url':r['source_url'],'as_of':'today'}]},[receipt(r)])['passed'])
        self.assertFalse(evidence_report({'category':'verified','sources':[{'url':r['items'][0]['article_url'],'as_of':'today'}]},[receipt(r)])['passed'])

    def test_empty_error_or_bad_news_does_not_count_as_evidence(self):
        for value in ({'code':0,'data':[]},{'code':True,'data':self.news()['data']},
                      {'code':2,'data':self.news()['data']}):
            with self.assertRaises(ValueError):futu_news('Tencent',fetcher=lambda u:value)
        for change in ({'url':'https://localhost/post/1'},{'publish_time':'invalid'},
                       {'title':''},{'publish_time':int(time.time())+99999}):
            d=self.news();d['data'][0].update(change)
            with self.assertRaises(ValueError):futu_news('Tencent',fetcher=lambda u:d)

    def test_timeline_sorts_pins_deduplicates_and_excludes_reposts(self):
        old=self.row(ts=int(time.time())-1000);old['is_pinned']=True
        new=self.row('2118674834632978756');new['replying_to']='public_person'
        foreign=self.row('2118674834632978757',handle='other')
        repost=self.row('2118674834632978758');repost['reposted_by']={'screen_name':'Alibaba_Qwen'}
        r=latest_x_posts('Alibaba_Qwen',lambda u:{'code':200,'results':[old,new,new,foreign,repost]})
        self.assertEqual(len(r['items']),2);self.assertEqual(r['items'][0]['id'],new['id'])
        self.assertTrue(r['items'][0]['is_reply']);self.assertIn('ONE',r['coverage'])

    def test_quote_separated_and_private_or_wrong_identity_rejected(self):
        row=self.row();row['quote']=self.row('2118674834632978757',handle='other')
        r=latest_x_posts('Alibaba_Qwen',lambda u:{'code':200,'results':[row]})
        self.assertEqual(r['items'][0]['quoted_post']['author'],'other')
        for change in ({'url':'https://x.com/wrong/status/'+row['id']},
                       {'author':{'screen_name':'Alibaba_Qwen','protected':True}},
                       {'created_timestamp':True},{'type':'tombstone'}):
            bad={**row,**change}
            with self.assertRaises(ValueError):latest_x_posts('Alibaba_Qwen',lambda u:{'code':200,'results':[bad]})

    def test_untrusted_urls_and_arguments(self):
        r=futu_news('Tencent',fetcher=lambda u:self.news());self.assertTrue(valid_url(r['source_url']))
        for u in ('http://127.0.0.1','https://api.fxtwitter.com/2/trends',
                  r['source_url']+'&size=50',r['source_url'].replace('https://','https://user@'),
                  r['source_url'].replace('futunn.com','futunn.com.evil.test')):
            self.assertFalse(valid_url(u))
        with self.assertRaises(ValueError):NoRedirect().redirect_request(None,None,None,None,None,None)
        for name,args in (('futu_news',{'keyword':'foo','news_type':True}),
                          ('latest_x_posts',{'handle':'../private'}),('futu_news',{'keyword':'http://x.com','news_type':1}),
                          ('latest_x_posts',{'handle':'Qwen','url':'http://localhost'})):
            with self.assertRaises(ValueError):run(name,args)

    def test_receipt_requires_success_hash_count_and_allowed_endpoint(self):
        r=futu_news('Tencent',fetcher=lambda u:self.news());e=receipt(r)
        for change in ({'success':False},{'count':0},{'count':True},{'content_sha256':''},{'url':'https://example.com'}):
            self.assertIsNone(source_url({**e,**change}))

    def model(self):
        c=Codex();c.events=queue.Queue();c.deadline=time.monotonic()+30;c.active_thread='a'
        c.public_feed_events=[];c.sent=[];c.send=c.sent.append
        return c

    def call(self,c,args=None,thread='a'):
        c.events.put({'id':1,'method':'item/tool/call','params':{'threadId':thread,
            'namespace':'public_research','tool':'futu_news','arguments':args or {'keyword':'Tencent','news_type':1}}})
        c.receive()

    def test_scoped_tool_reuses_only_same_turn_identical_result_and_bounded_calls(self):
        c=self.model();r=futu_news('Tencent',fetcher=lambda u:self.news())
        with patch('codex_client.run_public_research',return_value=r) as read:
            self.call(c,thread='other');self.assertEqual(read.call_count,0)
            self.call(c);self.call(c);self.assertEqual(read.call_count,1)
            self.call(c,{'keyword':'Apple','news_type':1});self.assertEqual(read.call_count,2)
            self.call(c,{'keyword':'Nvidia','news_type':1});self.assertEqual(read.call_count,2)
            self.assertFalse(c.sent[-1]['result']['success'])

    def test_failure_and_short_budget_do_not_produce_receipts_or_leak_errors(self):
        c=self.model()
        with patch('codex_client.run_public_research',side_effect=TimeoutError('private token')):self.call(c)
        self.assertEqual(c.public_feed_events,[]);self.assertNotIn('private token',json.dumps(c.sent))
        c.deadline=time.monotonic()+2
        with patch('codex_client.run_public_research') as read:self.call(c);read.assert_not_called()


if __name__=='__main__':unittest.main()
