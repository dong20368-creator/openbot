import hashlib
import json
from pathlib import Path
import queue
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch, Mock
import member_views as bridge
import member_window as window
from core import Store, Message, Worker
from test_core import Model, Sender
from codex_client import Codex


class MemberViewsTests(unittest.TestCase):
    def test_identity_ambiguity_mention_quote_and_self(self):
        roster=[{'username':'a','nick_name':'同名'},{'username':'b','nick_name':'同名'},
                {'username':'c','nick_name':'唯一'},{'username':'bot','nick_name':'助手'}]
        config={'account':'bot'}
        source={'sender':'a','source':'<msgsource><atuserlist>bot,c</atuserlist></msgsource>'}
        self.assertIsNone(window.choose('同名',roster,config,source))
        self.assertEqual(window.choose('唯一',roster,config,source),'c')
        self.assertEqual(window.choose('@mentioned',roster,config,source),'c')
        self.assertEqual(window.choose('@self',roster,config,source),'a')
        self.assertEqual(window.choose('@quoted',roster,config,source,'b'),'b')
        self.assertIsNone(window.choose('@quoted',roster,config,source,'outsider'))
        self.assertIsNone(window.choose('@mentioned',roster,config,{'sender':'a','text':'@唯一'}))
        self.assertIsNone(window.choose('助手',roster,config,source))

    def test_extract_boundaries_sender_tombstone_reply_and_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'message').mkdir();(root/'message/message_0.db').touch()
            table='Msg_'+hashlib.md5(b'room').hexdigest()
            db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
            db.execute('CREATE TABLE Name2Id(user_name TEXT)')
            db.executemany('INSERT INTO Name2Id VALUES(?)',[('a',),('b',),('bot',)])
            db.execute(f'CREATE TABLE {table}(server_id INTEGER,real_sender_id INTEGER,create_time INTEGER,sort_seq INTEGER,local_id INTEGER,local_type INTEGER,message_content TEXT)')
            rows=[(1,1,99,1,1,1,'before'),(2,1,100,2,2,1,'old withdrawn'),
                  (3,1,200,3,3,1,'after'),(4,2,150,4,4,1,'other person'),(5,1,160,5,5,34,'voice'),
                  (6,1,170,6,6,1,'讨论活动\n密码是secret123'),
                  (7,1,180,7,7,49,'<msg><appmsg><type>57</type><title>我不认同这个说法</title><refermsg><svrid>99</svrid><content>我看多</content></refermsg></appmsg></msg>')]
            db.executemany(f'INSERT INTO {table} VALUES(?,?,?,?,?,?,?)',rows)
            class Reader:
                def __init__(self,native=False):self.root=root;self.native=native
                def snapshot(self,path):
                    clone=sqlite3.connect(':memory:');clone.row_factory=sqlite3.Row;db.commit();db.backup(clone)
                    if self.native:clone.execute(f'UPDATE {table} SET local_type=10000 WHERE server_id=2')
                    return clone
            result=window.extract([Reader(),Reader(True)],{'account':'bot','group':'room'},'a',100,200)
            self.assertEqual([r['text'] for r in result['records']],['讨论活动','我不认同这个说法'])
            self.assertTrue(result['records'][1]['is_reply'])
            self.assertEqual(result['excluded_nontext'],2)
            self.assertNotIn('secret',json.dumps(result))
            self.assertNotIn('我看多',json.dumps(result,ensure_ascii=False))
            db.close()

    def test_bound_and_report_truncation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'message').mkdir();(root/'message/message_0.db').touch()
            table='Msg_'+hashlib.md5(b'room').hexdigest()
            class Reader:
                def snapshot(self,path):
                    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
                    db.execute('CREATE TABLE Name2Id(user_name TEXT)');db.execute("INSERT INTO Name2Id VALUES('a')")
                    db.execute(f'CREATE TABLE {table}(server_id INTEGER,real_sender_id INTEGER,create_time INTEGER,sort_seq INTEGER,local_id INTEGER,local_type INTEGER,message_content TEXT)')
                    db.executemany(f'INSERT INTO {table} VALUES(?,1,?,?,?,1,?)',[(i,i,i,i,'观点'*1000) for i in range(1,250)])
                    return db
            r=Reader();r.root=root
            result=window.extract([r],{'group':'room'},'a',0,300)
            self.assertEqual(result['matched_text_count'],249)
            self.assertGreater(result['omitted_for_size'],0)
            self.assertTrue(all(x['text_truncated'] for x in result['records']))
            self.assertLess(len(json.dumps(result,ensure_ascii=False)),26000)

    def test_scope_rejected_before_database_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'runtime').mkdir();(root/'runtime/account.json').write_text(json.dumps({'account':'bot','group':'room'}))
            with patch.object(window,'BASE',root),patch.object(window,'Reader') as reader:
                with self.assertRaises(ValueError):window.read({'account':'bot','group':'other'})
                with self.assertRaises(ValueError):window.read({'account':'bot','group':'room','member':'a','days':8,'end':int(time.time())})
                reader.assert_not_called()

    def test_bridge_binds_job_scope_strips_identity_and_honors_optout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'runtime').mkdir()
            store=Store(root/'runtime/state.sqlite3','bot',{'room'},100)
            job={'account':'bot','room':'room','sender':'asker','message_id':'s:123','created':1234}
            store.ingest(Message('bot','room','s:55','target',('bot',),'合成观点',101),now=102)
            Worker(store,Model(),Sender(),'send').run_one()
            result={'status':'ok','target_id':'target','records':[{'text':'合成观点','source_message_id':'s:55'}]}
            with patch.object(bridge,'ROOT',root),patch.object(bridge.subprocess,'run',return_value=Mock(returncode=0,stdout=json.dumps(result))) as run:
                loaded=bridge.load(job,{'member':'甲','days':3})
                self.assertNotIn('target_id',loaded)
                self.assertNotIn('source_message_id',loaded['records'][0])
                self.assertEqual(loaded['records'][0]['bot_answer'],'合成测试回答')
                sent=json.loads(run.call_args.kwargs['input']);self.assertEqual(sent['sender'],'asker');self.assertEqual(sent['end'],1234)
                store.forget('room','target')
                self.assertEqual(bridge.load(job,{'member':'甲','days':3})['status'],'unavailable')
            store.close()

    def test_backend_authenticates_source_sender_timestamp_and_real_mention(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'runtime').mkdir();(root/'runtime/account.json').write_text(json.dumps({'account':'bot','group':'room'}))
            now=int(time.time());request={'account':'bot','group':'room','sender':'a','message_id':'s:1','end':now,'days':3,'member':'@self'}
            source={'sender':'a','create_time':now,'local_type':1,'message_content':'总结我','source':'<msgsource><atuserlist>bot</atuserlist></msgsource>'}
            reader=Mock();reader.root=Path('/data/bot_profile/db_storage');reader.native=False
            with patch.object(window,'BASE',root),patch.object(window,'Reader',return_value=reader),patch('media_reader.row_for',return_value=source) as lookup,patch.object(window,'members',return_value=[{'username':'a','nick_name':'甲'}]),patch.object(window,'extract',return_value={'records':[]}) as extract:
                self.assertEqual(window.read(request)['status'],'ok')
                self.assertEqual(extract.call_args.args[2:],('a',now-3*86400,now))
                for change in ({'sender':'b'},{'create_time':now-5},{'source':'<msgsource/>'},{'local_type':10000}):
                    lookup.return_value={**source,**change}
                    with self.assertRaises(ValueError):window.read(request)
                self.assertEqual(extract.call_count,1)
                (root/'runtime/text-mention.json').write_text(json.dumps({'enabled_since':now-1}))
                lookup.return_value={**source,'source':'<msgsource/>','message_content':'@Openbot 总结我'}
                self.assertEqual(window.read(request)['status'],'ok')
                lookup.return_value={**source,'source':'<msgsource/>','message_content':'@OpenbotOther 总结我'}
                with self.assertRaises(ValueError):window.read(request)

    def test_invalid_parameters_fail_closed(self):
        for args in ({'member':'a','days':True},{'member':'a','days':8},{'member':'a','days':0},{'member':'a','days':3,'group':'other'},{'member':'','days':3}):
            self.assertFalse(bridge.validate(args))
        with patch.object(bridge.subprocess,'run') as run:
            with self.assertRaises(ValueError):bridge.load({}, {'member':'a','days':3})
            run.assert_not_called()

    def test_tool_thread_budget_failure_and_audit(self):
        model=Codex();model.events=queue.Queue();model.deadline=time.monotonic()+90;model.active_thread='real';model.current_job={'id':1};model.send=Mock()
        def event(thread='real',days=3):return {'id':1,'method':'item/tool/call','params':{'namespace':'group_views','tool':'read_member_statements','threadId':thread,'arguments':{'member':'a','days':days}}}
        with patch('codex_client.load_member_views',return_value={'status':'ok','records':[{}]}) as load:
            for e in (event('wrong'),event(days=8)):model.events.put(e);model.receive()
            load.assert_not_called()
            for _ in range(3):model.events.put(event());model.receive()
            self.assertEqual(load.call_count,2);self.assertEqual(model.member_view_records,2)
            self.assertFalse(model.send.call_args.args[0]['result']['success'])
        model.member_view_calls=0
        with patch('codex_client.load_member_views',side_effect=RuntimeError('sensitive path')):
            model.events.put(event());model.receive()
            self.assertNotIn('sensitive path',json.dumps(model.send.call_args.args[0]))

    def test_each_person_dialogue_persists_separately_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'state.db';store=Store(path,'bot',{'room'},100)
            for i,person in enumerate(('a','b'),1):
                store.ingest(Message('bot','room',str(i),person,('bot',),'same nickname, own '+person,101),now=102)
                Worker(store,Model(),Sender(),'send').run_one()
            store.close();store=Store(path,'bot',{'room'},100)
            for i,person in enumerate(('a','b'),3):
                store.ingest(Message('bot','room',str(i),person,('bot',),'继续',103),now=104)
                job=store.claim();history=store.history(job,True)
                self.assertEqual([x['question'] for x in history],['same nickname, own '+person])
                self.assertTrue(history[0]['answer'])
                store.transition(job['id'],'generating','preview')
            store.forget('room','a')
            self.assertEqual(store.db.execute("SELECT count(*) FROM jobs WHERE sender='b' AND state='confirmed'").fetchone()[0],1)
            store.close()

if __name__=='__main__':unittest.main()
