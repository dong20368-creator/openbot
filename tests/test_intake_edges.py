"""Synthetic intake faults; no WSL, network, production DB or real messages."""
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
from core import Store,Message
from inbox import read_recent,decode


class IntakeEdges(unittest.TestCase):
    def test_corrupt_rows_duplicate_group_names_and_outage_backfill(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent/'runtime') as tmp:
            base=Path(tmp);root=base/'bot'/'db_storage';(root/'message').mkdir(parents=True)
            (root/'message/message_0.db').touch();(base/'runtime').mkdir()
            (base/'runtime/account.json').write_text(json.dumps({'account':'bot','group':'room'}))
            contact=sqlite3.connect(':memory:');contact.row_factory=sqlite3.Row
            contact.execute('CREATE TABLE contact(username TEXT,nick_name TEXT,is_in_chat_room INTEGER)')
            contact.executemany('INSERT INTO contact VALUES(?,?,1)',[('room','same'),('other','same')])
            db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
            table='Msg_'+hashlib.md5(b'room').hexdigest()
            db.execute('CREATE TABLE Name2Id(user_name TEXT)');db.execute("INSERT INTO Name2Id VALUES('member')")
            db.execute(f'CREATE TABLE {table}(server_id INTEGER,real_sender_id INTEGER,create_time INTEGER,sort_seq INTEGER,local_id INTEGER,local_type INTEGER,message_content,source)')
            now=int(time.time());valid='<msgsource><atuserlist>bot</atuserlist></msgsource>'
            rows=[(1,b'\xff','bad'),(2,'<!DOCTYPE x><x/>','bad'),(3,valid,'x'*131073),(4,valid,'survives')]
            for ident,source,body in rows:
                db.execute(f'INSERT INTO {table} VALUES(?,1,?,?,?,1,?,?)',(ident,now-900,ident,ident,body,source))
            class Reader:
                native=True
                def snapshot(self,name):
                    original=contact if name.startswith('contact/') else db
                    clone=sqlite3.connect(':memory:');clone.row_factory=sqlite3.Row
                    original.commit();original.backup(clone);return clone
            reader=Reader();reader.root=root
            try:
                with patch('inbox.BASE',base):config,messages=read_recent(reader=reader)
                self.assertEqual([m['message_id'] for m in messages],['s:4'])
                self.assertEqual(config['unreadable_messages'],3)
                store=Store(base/'state.db','bot',{'room'},now-3600,max_queue_age=1800)
                try:
                    self.assertTrue(store.ingest(Message(**messages[0]),now=now))
                    self.assertFalse(store.ingest(Message(**messages[0]),now=now))
                    for age in (1801,-31):
                        candidate={**messages[0],'message_id':str(age),'created':now-age}
                        self.assertFalse(store.ingest(Message(**candidate),now=now))
                finally:store.close()
                # New policy accepts visible @name without native metadata,
                # but not old messages, another name, self messages or quoted mentions.
                (base/'runtime/text-mention.json').write_text(json.dumps({'enabled_since':now-60}))
                extra=[
                    (5,1,now,1,'@Openbot 你好',''),
                    (6,1,now,1,'＠openbot你好',''),
                    (7,1,now,1,'Openbot 你好',''),
                    (8,1,now,1,'@OpenbotOther hi',''),
                    (9,1,now-120,1,'@Openbot old',''),
                    (10,1,now,49,'<msg><appmsg><type>57</type><title>@Openbot 看看</title></appmsg></msg>',''),
                    (11,1,now,49,'<msg><appmsg><type>57</type><title>看看</title><refermsg><content>@Openbot</content></refermsg></appmsg></msg>',''),
                    (12,1,now,1,'@Openbot hi',valid),
                    (13,2,now,1,'@Openbot hi','')]
                db.execute("INSERT INTO Name2Id VALUES('bot')")
                for ident,sender,created,kind,body,source in extra:
                    db.execute(f'INSERT INTO {table} VALUES(?,?,?,?,?,?,?,?)',(ident,sender,created,ident,ident,kind,body,source))
                with patch('inbox.BASE',base): _,messages=read_recent(reader=reader)
                self.assertEqual({m['message_id'] for m in messages},{'s:4','s:5','s:6','s:10','s:12'})
                self.assertTrue(all(m['mentions']==('bot',) for m in messages))
                store=Store(base/'text-state.db','bot',{'room'},now-3600,max_queue_age=1800)
                try:
                    for message in messages:
                        self.assertTrue(store.ingest(Message(**message),now=now))
                        self.assertFalse(store.ingest(Message(**message),now=now))
                    candidate={**messages[-1],'group':'other','message_id':'other'}
                    self.assertFalse(store.ingest(Message(**candidate),now=now))
                finally:store.close()
                reader.native=False
                with patch('inbox.BASE',base),self.assertRaisesRegex(RuntimeError,'Ambiguous'):
                    read_recent(reader=reader)
            finally:contact.close();db.close()

    def test_string_and_bytes_have_equal_decoding_limits(self):
        for value in ('a'*131073,b'a'*131073,'中'*43691):
            with self.subTest(kind=type(value)),self.assertRaises(ValueError):decode(value)
        self.assertEqual(decode('你好'),decode('你好'.encode()))


if __name__=='__main__':unittest.main()
