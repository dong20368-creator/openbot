import json,unittest
from unittest.mock import patch,Mock
from media_context import fetch_media
from backend_preflight import verify

class RecentFixes(unittest.TestCase):
    def test_attachment_sync_retry_is_bounded_and_same_scope(self):
        request={'account':'bot','group':'room','message_id':'s:1'}
        missing=Mock(returncode=1,stdout=json.dumps({'error':'media_not_downloaded'}))
        success=Mock(returncode=0,stdout=json.dumps({'kind':'image','data':'synthetic'}))
        with patch('media_context.subprocess.run',side_effect=[missing,success]) as call,patch('media_context.time.sleep'):
            self.assertEqual(fetch_media(request)['kind'],'image')
            self.assertEqual(call.call_count,2)
            self.assertTrue(all(json.loads(c.kwargs['input'])==request for c in call.call_args_list))
        with patch('media_context.subprocess.run',return_value=missing) as call,patch('media_context.time.sleep'):
            with self.assertRaises(ValueError):fetch_media(request)
            self.assertEqual(call.call_count,2)
        denied=Mock(returncode=1,stdout=json.dumps({'error':'scope_mismatch'}))
        with patch('media_context.subprocess.run',return_value=denied) as call:
            with self.assertRaises(ValueError):fetch_media(request)
            self.assertEqual(call.call_count,1)

    def test_preflight_checks_service_directory_and_fails_closed(self):
        with patch('backend_preflight.subprocess.run',return_value=Mock(returncode=0,stdout='{"ok":true}')) as call:
            self.assertTrue(verify()['ok'])
            args=call.call_args.args[0]
            self.assertEqual(args[args.index('--cd')+1],'/opt/wechat-agent-v2')
        with patch('backend_preflight.subprocess.run',return_value=Mock(returncode=1,stdout='',stderr='private path')):
            with self.assertRaisesRegex(RuntimeError,'^backend_dependencies_unavailable$'):verify()

if __name__=='__main__':unittest.main()
