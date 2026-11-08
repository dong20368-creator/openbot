"""Synthetic intake -> queue -> media binding; no real pictures or sends."""
import hashlib,json,sqlite3,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from inbox import read_recent
from core import Store,Message
from media_context import MediaContext

class AutoImage(unittest.TestCase):
    def test_binding_boundaries_and_existing_reference(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent/'runtime') as tmp:
            base=Path(tmp);root=base/'bot'/'db_storage';(root/'message').mkdir(parents=True)
            (root/'message/message_0.db').touch();(base/'runtime').mkdir()
            (base/'runtime/media-consent.json').write_text(json.dumps({'openai_explicit_references':True}))
            (base/'runtime/account.json').write_text(json.dumps({'account':'bot','group':'room'}))
            now=int(time.time())
            (base/'runtime/auto-image.json').write_text(json.dumps({'enabled_since':now-100}))
            db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
            db.execute('CREATE TABLE Name2Id(user_name TEXT)');db.execute("INSERT INTO Name2Id VALUES('member')")
            db.execute("INSERT INTO Name2Id VALUES('other')")
            db.execute('CREATE TABLE contact(username TEXT,nick_name TEXT,is_in_chat_room INTEGER)')
            db.execute("INSERT INTO contact VALUES('room','room',1)")
            table='Msg_'+hashlib.md5(b'room').hexdigest()
            db.execute(f'CREATE TABLE {table}(server_id INTEGER,real_sender_id INTEGER,create_time INTEGER,sort_seq INTEGER,local_id INTEGER,local_type INTEGER,message_content TEXT,source TEXT)')
            def add(i,age,kind,text):
                db.execute(f'INSERT INTO {table} VALUES(?,1,?,?,?,?,?,?)',(i,now+age,i,i,kind,text,'<msgsource><atuserlist>bot</atuserlist></msgsource>'))
            class Reader:
                native=True
                def snapshot(self,name):
                    clone=sqlite3.connect(':memory:');clone.row_factory=sqlite3.Row
                    db.commit();db.backup(clone);return clone
            reader=Reader();reader.root=root
            add(1,-12,3,'image')
            add(2,-150,1,'@Openbot 这张图是什么') # predates activation
            add(3,-10,1,'@Openbot 这张图是什么')
            add(4,-9,1,'@Openbot 今天天气如何')
            add(5,-8,3,'image')
            add(6,-7,1,'@Openbot 这张图是什么') # latest own image
            add(7,-6,1,'@Openbot 最后一张图讲什么')
            add(8,-5,49,'<msg><appmsg><type>57</type><title>@Openbot 这张图</title><refermsg><svrid>1</svrid></refermsg></appmsg></msg>')
            add(9,-4,1,'@Openbot 比较这几张图')
            add(10,0,1,'@Openbot 最后一张图')
            add(11,0,3,'future image same second')
            add(12,1,3,'future image')
            add(13,2000,1,'@Openbot 这张图是什么') # stale
            add(14,-11,3,'intervening other member image')
            db.execute(f'UPDATE {table} SET real_sender_id=2 WHERE server_id=14')
            add(15,-10,1,'@Openbot 这张图是什么')
            db.execute(f'UPDATE {table} SET real_sender_id=2 WHERE server_id=15')
            add(16,-10,1,'@Openbot 图片是什么意思？')
            add(17,700,1,'@Openbot 最后一张图是什么意思？')
            add(18,2,1,'@Openbot 你怎么看？') # follows own image
            add(19,3,1,'@Openbot 什么意思？') # follows own text, use dialogue
            add(20,4,1,'@Openbot 上一张呢？')
            add(21,5,1,'@Openbot 这两张图有什么区别？')
            add(22,6,1,'@Openbot 第2张图说什么？')
            add(23,7,1,'@Openbot 这三张图有什么区别？')
            add(24,10,3,'own image')
            add(25,11,1,'@Openbot 这个男人穿衣打扮给打几分')
            add(26,12,1,'@Openbot 说得直接一些')
            add(27,13,3,'own image')
            add(28,14,1,'@Openbot 比较12和13哪个大')
            add(29,15,3,'other image')
            db.execute(f'UPDATE {table} SET real_sender_id=2 WHERE server_id=29')
            add(30,16,1,'@Openbot 你觉得如何')
            try:
                with patch('inbox.BASE',base):_,messages=read_recent(reader=reader)
                rows={m['message_id']:m for m in messages}
                self.assertIsNone(rows['s:2']['reference_data'])
                self.assertEqual(rows['s:3']['reference_id'],'s:1')
                self.assertEqual(rows['s:3']['reference_data']['association'],'previous_image')
                self.assertEqual(rows['s:3']['reference_data']['association_scope'],'own')
                self.assertEqual(rows['s:15']['reference_id'],'s:14')
                self.assertEqual(rows['s:16']['reference_id'],'s:1')
                self.assertEqual(rows['s:17']['reference_id'],'s:27')
                self.assertIsNone(rows['s:4']['reference_data'])
                self.assertEqual(rows['s:6']['reference_id'],'s:5')
                self.assertEqual(rows['s:7']['reference_id'],'s:5')
                self.assertEqual(rows['s:8']['reference_id'],'s:1')
                self.assertNotIn('association',rows['s:8']['reference_data'])
                self.assertNotIn('media_kind',rows['s:9']['reference_data'])
                self.assertEqual(rows['s:10']['reference_id'],'s:5')
                self.assertIsNone(rows['s:13']['reference_data'])
                self.assertEqual(rows['s:18']['reference_id'],'s:12')
                self.assertIsNone(rows['s:19']['reference_data'])
                self.assertEqual(rows['s:25']['reference_id'],'s:24')
                self.assertEqual(rows['s:25']['reference_data']['association'],'adjacent_own_image')
                self.assertIsNone(rows['s:26']['reference_data'])
                self.assertEqual(rows['s:28']['reference_id'],'s:27')
                self.assertEqual(rows['s:28']['reference_data']['association'],'adjacent_own_image')
                self.assertIsNone(rows['s:30']['reference_data'])
                self.assertEqual(rows['s:20']['reference_id'],'s:11')
                self.assertEqual(rows['s:21']['reference_id'],'s:11')
                self.assertEqual(rows['s:21']['reference_data']['chain'][0]['message_id'],'s:12')
                self.assertNotIn('media_kind',rows['s:22']['reference_data'])
                self.assertEqual([rows['s:23']['reference_id'],*[r['message_id'] for r in rows['s:23']['reference_data']['chain']]],['s:5','s:11','s:12'])
                store=Store(base/'queue.db','bot',{'room'},now-600,max_queue_age=1800)
                try:
                    msg=rows['s:3'];self.assertTrue(store.ingest(Message(**msg),now=now))
                    self.assertFalse(store.ingest(Message(**msg),now=now))
                    bad={**msg,'group':'other','message_id':'other'}
                    self.assertFalse(store.ingest(Message(**bad),now=now))
                    self.assertTrue(store.ingest(Message(**rows['s:21']),now=now+6))
                    job=dict(store.db.execute('SELECT * FROM jobs').fetchone())
                    with patch('media_context.Path',return_value=base/'media_context.py'), patch('media_context.fetch_media',side_effect=ValueError('media_not_downloaded')) as fetch:
                        media=MediaContext()(job)
                        self.assertEqual(fetch.call_args.args[0],{'account':'bot','group':'room','message_id':'s:1'})
                        self.assertTrue(media['notes'][0]['unavailable'])
                    pair=dict(store.db.execute("SELECT * FROM jobs WHERE message_id='s:21'").fetchone())
                    with patch('media_context.Path',return_value=base/'media_context.py'), patch('media_context.fetch_media',side_effect=ValueError('media_not_downloaded')) as fetch:
                        media=MediaContext()(pair)
                        self.assertEqual([c.args[0]['message_id'] for c in fetch.call_args_list],['s:11','s:12'])
                finally:store.close()
            finally:db.close()

if __name__=='__main__':unittest.main()
