import time
import unittest
from unittest.mock import Mock,patch
from codex_client import Codex


class WarmTransportTests(unittest.TestCase):
    def test_child_explicitly_disables_unrelated_mcp_servers(self):
        import json
        m=Codex();m.rpc=Mock();m.send=Mock()
        proc=Mock();proc.stdout=[]
        with patch('codex_client.binary',return_value='codex'),patch('codex_client.environment',return_value={}),patch('codex_client.subprocess.Popen',return_value=proc) as spawn:
            m._start();m.reader_thread.join(1)
        args=spawn.call_args.args[0]
        settings={key:json.loads(value) for key,value in (args[i+1].split('=',1) for i,x in enumerate(args) if x=='-c')}
        for name in ('codex_apps','xcodebuildmcp'):
            self.assertIs(settings['mcp_servers.'+name+'.enabled'],False)
        self.assertNotIn('mcp_servers.futu_research.enabled',settings)

    def model(self):
        m=Codex(reuse_process=True)
        m.proc=Mock();m.proc.poll.return_value=None
        m.process_started=time.monotonic();m.process_requests=1
        m.close=Mock();m._start=Mock();m.backlog=['old']
        return m

    def test_live_transport_reused_and_backlog_reset(self):
        m=self.model();m.prepare_process()
        self.assertTrue(m.connection_reused);m._start.assert_not_called()
        self.assertEqual(m.backlog,[]);self.assertEqual(m.process_requests,2)

    def test_dead_old_or_request_limit_restarts(self):
        for reason in ('dead','age','limit'):
            with self.subTest(reason=reason):
                m=self.model()
                if reason=='dead':m.proc.poll.return_value=1
                if reason=='age':m.process_started-=1801
                if reason=='limit':m.process_requests=32
                m.prepare_process();m.close.assert_called_once();m._start.assert_called_once()
                self.assertFalse(m.connection_reused)

    def test_cleanup_waits_for_actual_unload(self):
        m=self.model();m.active_thread='a'
        m.rpc=Mock(side_effect=[{'status':'unsubscribed'},{'data':['a']},{'data':[]}])
        with patch('codex_client.time.sleep'):m.finish_request(True)
        self.assertIsNone(m.active_thread);m.close.assert_not_called()
        self.assertEqual(m.rpc.call_count,3)

    def test_failed_or_unverified_cleanup_discards_process(self):
        for results in ([{'status':'unexpected'}],[{'status':'unsubscribed'},{}],
                        [RuntimeError('offline')]):
            m=self.model();m.active_thread='a';m.rpc=Mock(side_effect=results)
            m.finish_request(True);m.close.assert_called_once()
        m=self.model();m.active_thread='a';m.finish_request(False)
        m.close.assert_called_once();self.assertIsNone(m.active_thread)

    def test_warmup_has_no_model_turn_and_unloads_thread(self):
        m=self.model();m.rpc=Mock(side_effect=[{'account':{'type':'chatgpt'}},
            {'thread':{'id':'warm'}},{'status':'unsubscribed'},{'data':[]}])
        m.warmup()
        self.assertEqual([c.args[0] for c in m.rpc.call_args_list],
            ['account/read','thread/start','thread/unsubscribe','thread/loaded/list'])
        self.assertTrue(m.rpc.call_args_list[1].args[1]['ephemeral'])
        m.close.assert_not_called()

    def test_warmup_failure_is_optional_and_closes(self):
        m=self.model();m.rpc=Mock(side_effect=RuntimeError('offline'))
        m.warmup();m.close.assert_called_once()


if __name__=='__main__':unittest.main()
