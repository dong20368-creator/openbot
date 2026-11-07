import unittest,json
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
import inbox


class NativeHealthTests(unittest.TestCase):
    def test_busy_inbox_does_not_postpone_offline_detection(self):
        now=[10.0];checks=[];events=[]
        root=Path('synthetic');reader=SimpleNamespace(root=root,decryptions=0)
        def online():
            checks.append(now[0]);return 'online' if len(checks)<3 else 'native_reconnecting'
        def emit(value,**kwargs):
            event=json.loads(value);events.append(event)
            if event.get('kind')=='error':raise BrokenPipeError()
        def stat(*args):return SimpleNamespace(st_ino=1,st_size=1,st_mtime_ns=now[0])
        with patch.object(inbox,'database_roots',return_value=[root]),patch.object(inbox,'Reader',return_value=reader),\
            patch('native_connection_health.status',side_effect=online),patch.object(inbox,'read_recent',return_value=({},[])),\
            patch.object(inbox.time,'monotonic',side_effect=lambda:now[0]),\
            patch.object(inbox.time,'sleep',side_effect=lambda seconds:now.__setitem__(0,now[0]+3)),\
            patch.object(Path,'stat',stat),patch.object(Path,'glob',return_value=[]),patch('builtins.print',side_effect=emit):
            with self.assertRaises(BrokenPipeError):inbox.watch()
        self.assertEqual(len(checks),3)
        self.assertLessEqual(checks[-1]-checks[0],12)
        self.assertTrue(any(event.get('error_code')=='native_reconnecting' for event in events))


if __name__=='__main__':unittest.main()
