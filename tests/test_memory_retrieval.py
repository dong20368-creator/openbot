import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from core import Store,Message
from context_budget import bounded_history
import dialogue_history as archive
from answer_limits import MAX_RESEARCH_REQUESTS
from public_research import X_DISCOVERY_GUIDANCE

class MemoryRetrieval(unittest.TestCase):
    def setUp(self):self.s=Store(':memory:','bot',{'room'},0)
    def tearDown(self):self.s.close()
    def add(self,ident,q,a='收到',sender='alice',state='confirmed'):
        self.s.ingest(Message('bot','room',str(ident),sender,('bot',),q,100+ident),now=100+ident)
        self.s.db.execute('UPDATE jobs SET answer=?,state=? WHERE message_id=?',(a,state,str(ident)))
        row=dict(self.s.db.execute('SELECT * FROM jobs WHERE message_id=?',(str(ident),)).fetchone())
        if state=='confirmed':self.s.index_job(row)
        return row
    def test_old_relevant_record_survives_recent_chatter(self):
        self.add(1,'我的合成航班代码是AB7349。'+'详细行程说明。'*100)
        for i in range(2,42):self.add(i,'普通讨论'+('背景说明。'*500))
        job=self.add(42,'我以前说的航班代码是多少',state='generating')
        old=bounded_history('航班',self.s.history({**job,'question':'航班'},True),budget=2500)
        self.assertNotIn('AB7349',json.dumps(old,ensure_ascii=False))
        result=bounded_history('航班',self.s.search_history(job,'航班',True),budget=6000)
        self.assertIn('AB7349',json.dumps(result,ensure_ascii=False))
        self.assertEqual(len(result),1)
    def test_no_results_does_not_return_unrelated_recent_turns(self):
        self.add(1,'普通讨论');job=self.add(2,'查询',state='generating')
        self.assertEqual(self.s.search_history(job,'不存在的主题',True),[])
    def test_semantic_candidate_is_revalidated_and_forget_is_respected(self):
        other=self.add(1,'航班代码OTHER',sender='bob');mine=self.add(2,'航班代码OWN')
        job=self.add(3,'查询',state='generating')
        self.s.semantic=lambda j:[other,mine,job]
        result=self.s.search_history(job,'航班',True)
        self.assertEqual([x['message_id'] for x in result],['2'])
        self.s.forget('room','alice')
        self.assertEqual(self.s.search_history(job,'航班',True),[])
    def test_archive_retains_final_correction(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()/'runtime') as tmp:
            root=Path(tmp);(root/'runtime').mkdir()
            s=Store(root/'runtime/state.sqlite3','bot',{'room'},0)
            s.ingest(Message('bot','room','old','alice',('bot',),'活动说明。'*1000+'最后更正为周四。',100),now=100)
            s.db.execute("UPDATE jobs SET state='confirmed',answer=?",('背景说明。'*2000+'最终预算4200元。',))
            s.ingest(Message('bot','room','new','alice',('bot',),'回顾最后更正的日期和预算',200),now=200)
            job=dict(s.db.execute("SELECT * FROM jobs WHERE message_id='new'").fetchone())
            with patch.object(archive,'ROOT',root):result=archive.load(job,{'days':30,'before_id':0})
            record=result['records'][0]
            self.assertIn('最后更正为周四',record['question']);self.assertIn('最终预算4200',record['bot_answer'])
            self.assertTrue(record['excerpt']);self.assertLessEqual(len(record['question']),2000)
            self.assertLessEqual(len(record['bot_answer']),3000);s.close()
    def test_tool_guidance_uses_shared_budget(self):
        self.assertIn(f'共用最多{MAX_RESEARCH_REQUESTS}次新请求',X_DISCOVERY_GUIDANCE)
        self.assertNotIn('两个新读取回合',X_DISCOVERY_GUIDANCE)

if __name__=='__main__':unittest.main()
