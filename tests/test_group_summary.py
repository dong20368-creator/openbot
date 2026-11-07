import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from datetime import datetime
from unittest.mock import patch
from group_summary import TZ,is_summary,scrub,build_material,compact_summary
from group_window import extract,read
from enqueue_summary import window_end

class SummaryTests(unittest.TestCase):
    def test_summary_removes_empty_lines_and_padding(self):
        self.assertEqual(compact_summary('  重点：讨论活动\n\n\t结论：  待定\n \n 待办：查天气  '),
            '重点：讨论活动\n结论： 待定\n待办：查天气')
    def test_schedule_beijing_and_late_runs(self):
        now=datetime(2026,11,7,9,1,tzinfo=TZ).timestamp()
        self.assertEqual(window_end(now),int(datetime(2026,11,7,9,tzinfo=TZ).timestamp()))
        with self.assertRaises(ValueError):window_end(now+7200)

    def test_internal_summary_ids_only(self):
        self.assertTrue(is_summary({'message_id':'summary:123'}))
        self.assertFalse(is_summary({'message_id':'s:123','question':'summary:123'}))

    def test_scrub_drops_sensitive_lines_and_contacts(self):
        text=scrub('讨论周末活动\n密码是secret123\n联系13812345678或者test@example.org\nhttps://example.org/private?token=secret')
        self.assertIn('周末活动',text)
        for item in ('secret','13812345678','test@example.org'):self.assertNotIn(item,text)

    def test_size_limit_is_reported_not_claimed_complete(self):
        result=build_material({'start':0,'end':43200,'excluded':{},'messages':[
            {'speaker':'a','created':1,'text':'主题'*100} for _ in range(3)]},max_chars=300)
        self.assertGreater(result['omitted_for_size'],0)
        self.assertEqual(result['total_text_messages'],3)

    def test_window_boundaries_and_account_scope(self):
        import hashlib
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent/'runtime') as tmp:
            root=Path(tmp);(root/'message').mkdir();(root/'message/message_0.db').touch()
            db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
            table='Msg_'+hashlib.md5(b'room').hexdigest()
            db.execute('CREATE TABLE Name2Id(user_name TEXT)')
            db.executemany('INSERT INTO Name2Id VALUES(?)',[('member',),('bot',)])
            db.execute(f'CREATE TABLE {table}(server_id INTEGER,real_sender_id INTEGER,create_time INTEGER,sort_seq INTEGER,local_id INTEGER,local_type INTEGER,message_content TEXT)')
            rows=[(1,1,99,1,1,1,'before'),(2,1,100,2,2,1,'inside'),(3,1,200,3,3,1,'after'),(4,2,150,4,4,1,'bot answer'),(5,1,160,5,5,34,'voice')]
            db.executemany(f'INSERT INTO {table} VALUES(?,?,?,?,?,?,?)',rows)
            class Reader:
                def snapshot(self,*args):
                    clone=sqlite3.connect(':memory:');clone.row_factory=sqlite3.Row;db.commit();db.backup(clone);return clone
            r=Reader();r.root=root
            result=extract(r,{'account':'bot','group':'room'},100,200)
            self.assertEqual([m['text'] for m in result['messages']],['inside'])
            self.assertNotEqual(result['messages'][0]['speaker'],'member')
            self.assertEqual(result['excluded']['assistant'],1)
            self.assertEqual(result['excluded']['nontext'],1)
            # A newer native tombstone must suppress an older text projection.
            legacy=r
            class NativeReader:
                native=True
                def snapshot(self,*args):
                    clone=legacy.snapshot(*args)
                    clone.execute(f'UPDATE {table} SET local_type=10000,message_content=? WHERE server_id=2',('recalled',))
                    return clone
            native=NativeReader();native.root=root
            with patch('group_window.Reader',return_value=legacy):
                merged=extract(native,{'account':'bot','group':'room'},100,200)
            self.assertEqual(merged['messages'],[])
            db.close()

    def test_cross_group_rejected_before_database_read(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent/'runtime') as tmp:
            root=Path(tmp);(root/'runtime').mkdir()
            (root/'runtime/account.json').write_text(json.dumps({'account':'bot','group':'room'}))
            with patch('group_window.BASE',root),patch('group_window.Reader') as reader:
                with self.assertRaises(ValueError):read({'account':'bot','group':'other','end':0})
                reader.assert_not_called()

if __name__=='__main__':unittest.main()
