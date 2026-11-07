import unittest,json
from unittest.mock import Mock,patch
from native_connection_health import status
from collector import Collector

class ConnectionHealth(unittest.TestCase):
    def probe(self,account,group=None):
        transport=Mock();transport.base='/account'
        transport.request.side_effect=[{'serverId':'server'},account,group or {'id':'room','isInGroup':True}]
        with patch('native_connection_health.settings',return_value=({'server_id':'server'},{'account':'bot','group':'room'})),patch('native_connection_health.client',return_value=transport):
            result=status()
        transport.close.assert_called_once();return result,transport

    def test_auth_failure_is_not_network_retry(self):
        result,t=self.probe({'authenticated':False,'connection':'disconnected'})
        self.assertEqual(result,'native_auth_required');self.assertEqual(t.request.call_count,2)
        self.assertEqual(self.probe({'authenticated':True,'ownUsername':'bot','connection':'disconnected'})[0],'native_reconnecting')

    def test_online_scope_and_unavailable(self):
        account={'authenticated':True,'ownUsername':'bot','connection':'online'}
        self.assertEqual(self.probe(account)[0],'online')
        self.assertEqual(self.probe({**account,'ownUsername':'other'})[0],'native_target_changed')
        self.assertEqual(self.probe(account,{'id':'other','isInGroup':True})[0],'native_target_changed')
        with patch('native_connection_health.settings',side_effect=RuntimeError('secret')):
            self.assertEqual(status(),'native_transport_unavailable')

    def test_failure_and_recovery_events_deduplicated_without_sends(self):
        c=Collector('unused',{'account':'bot','group':'room'},0);store=Mock()
        error={'kind':'error','error_code':'native_auth_required'}
        c.accept(store,error);c.accept(store,error)
        self.assertEqual(store.event.call_count,1)
        self.assertFalse(c.snapshot()[0]['healthy'])
        self.assertEqual(c.snapshot()[0]['last_disconnect_reason'],'native_auth_required')
        c.accept(store,{'kind':'heartbeat'});c.accept(store,{'kind':'heartbeat'})
        self.assertEqual(store.event.call_count,2)
        self.assertTrue(c.snapshot()[0]['healthy']);self.assertIsNone(c.snapshot()[0]['reason'])
        c.accept(store,{'kind':'error','error_code':'sensitive path'})
        self.assertNotIn('sensitive',json.dumps(c.snapshot()[0]))
        store.ingest.assert_not_called()

if __name__=='__main__':unittest.main()
