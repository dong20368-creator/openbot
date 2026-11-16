import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import model_provider

class ProviderTests(unittest.TestCase):
 def test_explicit_selection_and_no_fallback(self):
  with tempfile.TemporaryDirectory() as directory:
   config=Path(directory)/'provider.json'
   with patch.object(model_provider,'CONFIG',config):
    self.assertEqual(model_provider.selection()['provider'],'codex')
    config.write_text(json.dumps({'provider':'antigravity','model':'gemini-4-argon-medium'}))
    with patch('argon_client.Argon',side_effect=RuntimeError('unavailable')):
     with self.assertRaisesRegex(RuntimeError,'unavailable'):model_provider.create_model()
    config.write_text(json.dumps({'provider':'antigravity','model':'unauthorized'}))
    with self.assertRaises(ValueError):model_provider.selection()

if __name__=='__main__':unittest.main()
