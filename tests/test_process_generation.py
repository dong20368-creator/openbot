"""A late reader from an old model process must never poison a new request."""
import queue
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from codex_client import Codex
from core import model_error_code

class ProcessIsolation(unittest.TestCase):
    def test_fault_codes_are_allowlisted_without_logging_upstream_secrets(self):
        self.assertEqual(model_error_code(RuntimeError('codex_process_ended')),'codex_process_ended')
        self.assertEqual(model_error_code(RuntimeError('codex_rpc_failed:thread/start')),'codex_rpc_failed:thread/start')
        self.assertEqual(model_error_code(RuntimeError('codex_rpc_failed:secret-token')),'unclassified')
        self.assertEqual(model_error_code(RuntimeError('password=synthetic')),'unclassified')

    def test_late_old_process_events_stay_in_old_queue(self):
        gates=[threading.Event(),threading.Event()]
        class Stream:
            def __init__(self,i):self.i=i
            def __iter__(self):
                gates[self.i].wait(3)
                yield '{"method":"old-output"}\n'
        model=Codex();model.rpc=lambda *a:{};model.send=lambda *a:None
        processes=[SimpleNamespace(stdout=Stream(i)) for i in range(2)]
        with patch('codex_client.binary',return_value='fake'),patch('codex_client.environment',return_value={}),patch('codex_client.subprocess.Popen',side_effect=processes):
            model._start();old_queue=model.events;old_reader=model.reader_thread
            model._start();new_queue=model.events;new_reader=model.reader_thread
            try:
                gates[0].set();old_reader.join(2)
                self.assertFalse(old_reader.is_alive())
                self.assertEqual(old_queue.get_nowait()['method'],'old-output')
                self.assertEqual(old_queue.get_nowait()['method'],'local/processEnded')
                with self.assertRaises(queue.Empty):new_queue.get_nowait()
            finally:gates[1].set();new_reader.join(2)

if __name__=='__main__':unittest.main()
