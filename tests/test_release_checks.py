import importlib.util,json,unittest
from pathlib import Path
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('release_check',Path(__file__).resolve().parents[1]/'tools/check_release.py')
release=importlib.util.module_from_spec(spec);spec.loader.exec_module(release)

class ReleaseChecks(unittest.TestCase):
    def identity(self,parent=''):
        return (parent+'\nOpenBot contributors\ncontributors@openbot.invalid\nOpenBot contributors\ncontributors@openbot.invalid\n').encode()
    def test_multiple_anonymous_commits_are_supported(self):
        def git(*args):return b'a\nb\n' if args[0]=='rev-list' else self.identity('a' if args[-1]=='b' else '')
        with patch.object(release,'git',side_effect=git):
            self.assertEqual(release.check_history(),[])
            self.assertTrue(release.check_history(single_root=True))
    def test_identity_check_covers_names_and_committer(self):
        for good,bad in ((b'OpenBot contributors',b'Personal author'),(b'contributors@openbot.invalid',b'person@example.invalid')):
            with patch.object(release,'git',side_effect=[b'a\n',self.identity().replace(good,bad,1)]):
                self.assertEqual(release.check_history()[0]['reason'],'nonanonymous_commit_metadata')
    def manifest_check(self,rows):
        data=json.dumps({'files':rows}).encode()
        index=b'100644 abc 0\tassistant/example.py\0' + b'100644 def 0\tMANIFEST.json\0'
        with patch.object(release,'git',side_effect=[data,index]):return release.check_manifest()
    def test_exact_staged_manifest_matches(self):
        self.assertEqual(self.manifest_check([dict(path='assistant/example.py',git_blob_sha='abc',mode='100644')]),[])
    def test_manifest_detects_stale_hash_missing_or_duplicate_entries(self):
        row=dict(path='assistant/example.py',git_blob_sha='abc',mode='100644')
        for rows in ([],[dict(row,git_blob_sha='old')],[row,row]):
            self.assertEqual(self.manifest_check(rows)[0]['reason'],'manifest_index_mismatch')

if __name__=='__main__':unittest.main()
