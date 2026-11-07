import json
from pathlib import Path
import queue
import tempfile
import threading
import time
import unittest
from collector import Collector
from core import Message,Store,Worker
from codex_client import Codex
from context_budget import bounded_history,estimated_tokens
from test_core import Sender


class ArchitectureTests(unittest.TestCase):
    def test_recent_person_turn_survives_many_long_facts(self):
        history=[{'kind':'current_person_explicit_facts','facts':[
            {'fact_key':str(i),'value':'旧偏好'*200,'updated':i} for i in range(32)]},
            {'kind':'current_person_history','continuity_role':'latest_turn','question':'本次选蓝色',
             'answer':'好的，蓝色','created':100}]
        selected=bounded_history('刚才选哪个颜色',history,budget=4000)
        self.assertTrue(any(x.get('question')=='本次选蓝色' for x in selected))
        self.assertLessEqual(estimated_tokens(selected),4000)
    def test_intake_continues_while_model_blocks(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent/'runtime') as tmp:
            path=Path(tmp)/'state.db'; now=time.time()
            main=Store(path,'bot',{'room'},now-60)
            main.ingest(Message('bot','room','first','a',('bot',),'问题一',now))
            entered=threading.Event(); release=threading.Event(); errors=[]
            class Slow:
                def answer(self,*args): entered.set(); release.wait(5); return '答复'
            def work():
                try:
                    s=Store(path,'bot',{'room'},now-60)
                    Worker(s,Slow(),Sender(),'send').run_one(); s.close()
                except Exception as exc: errors.append(type(exc).__name__)
            thread=threading.Thread(target=work); thread.start()
            self.assertTrue(entered.wait(3))
            collector=Collector(path,{'account':'bot','group':'room'},now-60)
            collector.accept(main,{'kind':'batch','config':{'account':'bot','group':'room'},'messages':[
                dict(account='bot',group='room',message_id='second',sender='b',mentions=['bot'],text='问题二',created=now)]})
            self.assertEqual(main.status().get('pending'),1)
            release.set(); thread.join(5)
            self.assertFalse(errors); self.assertEqual(main.status(),{'confirmed':1,'pending':1}); main.close()

    def test_context_total_is_bounded_including_person_facts(self):
        history=[{'kind':'current_person_explicit_facts','facts':[
            {'fact_key':str(i),'value':'偏好'*500,'updated':i} for i in range(32)]}]
        history += [dict(kind='current_person_history',question='显卡配置'*1500,answer='回答'*1000,created=i) for i in range(15)]
        selected=bounded_history('显卡配置是什么',history,budget=4000)
        self.assertLessEqual(estimated_tokens(selected),4000)
        self.assertTrue(any(x['kind']=='current_person_explicit_facts' for x in selected))

    def test_client_tool_refuses_cross_scope_and_unlisted_tools(self):
        c=Codex(); c.deadline=time.monotonic()+5; c.events=queue.Queue(); c.active_thread='allowed'
        c.memory_calls=0; sent=[]; lookups=[]
        c.send=sent.append; c.retriever=lambda q:lookups.append(q) or []
        for params in [dict(threadId='other',namespace='wechat_memory',tool='search',arguments={'query':'x'}),
                       dict(threadId='allowed',namespace='wechat_memory',tool='search',arguments={'query':'x','sender':'other'}),
                       dict(threadId='allowed',namespace='shell',tool='exec',arguments={'query':'x'})]:
            c.events.put({'id':1,'method':'item/tool/call','params':params}); c.receive()
        self.assertEqual(lookups,[])
        self.assertEqual(len(sent),3)

    def test_history_tool_call_budget(self):
        c=Codex(); c.deadline=time.monotonic()+5; c.events=queue.Queue(); c.active_thread='allowed'
        c.memory_calls=0; sent=[]; lookups=[]
        c.send=sent.append; c.retriever=lambda q:lookups.append(q) or []
        for i in range(4):
            c.events.put({'id':i,'method':'item/tool/call','params':dict(threadId='allowed',namespace='wechat_memory',tool='search',arguments={'query':'x'})})
            c.receive()
        self.assertEqual(len(lookups),3); self.assertFalse(sent[-1]['result']['success'])

if __name__=='__main__': unittest.main()
