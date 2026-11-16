import sys,json,time,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from argon_client import Argon
from reply_contract import ANSWER_SCHEMA
class TransportTests(unittest.TestCase):
 def client(self,outputs):
  b=Argon();b.system='unique-policy-fixture';b.tools=[];b.active_thread='t';b.session='s';b.backlog=[];b.transport_rounds=0;b.format_repairs=0
  captures=[];it=iter(outputs)
  def request(method,params):
   captures.append(params);b.text_parts=[next(it)];return {'stopReason':'end_turn'}
  b._request=request
  return b,captures
 def good(self):return {'category':'conversation','answer':'ok','model_disclosure':False,'unavailable_reason':'none','sources':[],'memories':[]}
 def run_turn(self,b):return b.rpc('turn/start',{'input':[{'type':'text','text':'synthetic question'}],'outputSchema':ANSWER_SCHEMA})
 def test_valid_json_missing_field_repaired(self):
  incomplete=self.good();del incomplete['memories'];b,c=self.client([json.dumps(incomplete),json.dumps(self.good())])
  self.run_turn(b);self.assertEqual(len(c),2)
 def test_validate_whole_batch_before_any_call(self):
  invalid={'tool_calls':[{'name':'synthetic','arguments':{}},{'name':[]} ]}
  valid={'tool_calls':[{'name':'synthetic','arguments':{}}]}
  b,c=self.client([json.dumps(invalid),json.dumps(valid),json.dumps(self.good())]);b._local_call=Mock(return_value={'content':'synthetic result'})
  self.run_turn(b);self.assertEqual(b._local_call.call_count,1)
 def test_bad_schema_fails_after_one_repair(self):
  b,c=self.client(['{}','{}',json.dumps(self.good())])
  with self.assertRaises(ValueError):self.run_turn(b)
  self.assertEqual(len(c),2)
 def test_full_policy_delivery_preserved(self):
  b,c=self.client([json.dumps(self.good())]);self.run_turn(b)
  self.assertEqual(sum(x.get('text')==b.system for x in c[0]['prompt']),1)
 def test_double_json_repair_has_no_tool_replay(self):
  b,c=self.client(['{} {}',json.dumps(self.good())]);b._local_call=Mock();self.run_turn(b);b._local_call.assert_not_called()
 def test_web_cache_scoped_to_one_request(self):
  b=Argon();b.tool_map={'public_web__read':('public_web','read')};b.web_cache={};b.web_reads=0;b.web_cache_hits=0;b.public_feed_events=[];b.deadline=time.monotonic()+40
  response=Mock(status=200,headers={'Content-Type':'text/plain'});response.stream.return_value=[b'Public synthetic content. '*20]
  pool=Mock();pool.__enter__=Mock(return_value=pool);pool.__exit__=Mock(return_value=False);pool.request.return_value=response
  with patch('argon_client.socket.getaddrinfo',return_value=[(2,1,6,'',('1.1.1.1',443))]),patch('argon_client.urllib3.HTTPSConnectionPool',return_value=pool):
   for ident in ('a','b'):
    result=b._local_call({'id':ident,'name':'public_web__read','input':{'url':'https://example.org/article'}})
    self.assertFalse(result.get('is_error',False))
   self.assertEqual(pool.request.call_count,1)
   self.assertEqual(len(b.public_feed_events),1)
  with patch.object(b,'_setup_state'):
   b.prepare_process();self.assertEqual(b.web_cache,{})
 def test_known_transport_error_is_classified(self):
  from core import model_error_code
  self.assertEqual(model_error_code(TimeoutError('argon_timeout')),'argon_timeout')
 def test_warmup_only_prepares_read_only_host(self):
  b=Argon();b._mcp_tools=Mock();b._request=Mock()
  b.warmup();b._mcp_tools.assert_called_once();b._request.assert_not_called()
  self.assertEqual(b.tool_map,{})
 def test_transport_timing_records_failure_without_payload(self):
  b=Argon();b._request_inner=Mock(side_effect=TimeoutError('argon_timeout'))
  with self.assertRaises(TimeoutError):b._request('session/prompt',{'private':'never log'})
  self.assertEqual(set(b.transport_seconds),{'session/prompt'})
  self.assertGreaterEqual(b.transport_seconds['session/prompt'],0)
 def test_public_context_timestamp_tie_preserves_prior_not_future(self):
  from core import Store,Message
  s=Store(':memory:','bot',{'room'},0)
  try:
   for i in (1,2,3):
    s.ingest(Message('bot','room',str(i),'other',('bot',),'synthetic',100),now=100)
    s.db.execute("UPDATE jobs SET state='confirmed',answer=? WHERE id=?",('reply'+str(i),i))
    s.db.execute("INSERT INTO job_events(job_id,event,created,metadata) VALUES(?,'confirmed',200,'{}')",(i,))
   h=s.history({'id':2,'account':'bot','room':'room','sender':'asker','created':200,'question':'shorter','reference_id':None},True)
   self.assertEqual([x['answer'] for x in h],['reply1'])
  finally:s.close()
if __name__=='__main__':unittest.main()
