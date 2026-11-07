import json
import unittest
from unittest.mock import Mock,patch
from collector import Collector
import test_analysis_reply


class LatencyContract(unittest.TestCase):
    def test_clock_changes_without_changing_instruction_prefix(self):
        captured=[];prompts=[]
        for clock in ('2026-11-07 14:00 UTC','2026-11-07 14:01 UTC'):
            m=test_analysis_reply.EvidenceRepairTests().model(first_category='conversation')
            rpc=m.rpc
            def capture(method,params):
                if method=='thread/start':captured.append(params['developerInstructions'])
                if method=='turn/start':prompts.append(json.loads(params['input'][0]['text']))
                return rpc(method,params)
            m.rpc=capture
            with patch('codex_client.time.strftime',return_value=clock):m.answer('hello')
        self.assertEqual(captured[0],captured[1])
        self.assertNotEqual(prompts[0]['系统UTC时间'],prompts[1]['系统UTC时间'])
        self.assertEqual(prompts[0]['本次问题'],'hello')

    def test_intake_wakes_only_after_durable_insert_and_health_update(self):
        c=Collector(None,{'account':'bot','group':'room'},0);store=Mock()
        store.ingest.return_value=True;seen=[]
        c.on_jobs=lambda:seen.append((store.ingest.call_count,c.snapshot()[0]['healthy']))
        from core import Message
        import dataclasses
        message=dataclasses.asdict(Message('bot','room','one','member',('bot',),'hello',1))
        c.accept(store,dict(kind='batch',config=c.config,messages=[message]))
        self.assertEqual(seen,[(1,True)])
        c.accept(store,dict(kind='heartbeat'))
        store.ingest.return_value=False
        c.accept(store,dict(kind='batch',config=c.config,messages=[message]))
        self.assertEqual(len(seen),1)


if __name__=='__main__':unittest.main()
