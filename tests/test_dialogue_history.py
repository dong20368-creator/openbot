import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from core import Store,Message,Worker
from test_core import Model,Sender
import dialogue_history as archive

class DialogueTests(unittest.TestCase):
    def test_long_continuity_archive_pagination_identity_and_forget(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()/'runtime') as tmp:
            root=Path(tmp);(root/'runtime').mkdir()
            db=root/'runtime/state.sqlite3';s=Store(db,'bot',{'room'},100)
            for i in range(40):
                s.ingest(Message('bot','room','s:'+str(i+1),'alice',('bot',),'话题'+str(i)+'内容'*100,101+i),now=141)
                Worker(s,Model(),Sender(),'send').run_one()
            s.ingest(Message('bot','room','s:99','bob',('bot',),'其他成员的私有记录',145),now=146)
            Worker(s,Model(),Sender(),'send').run_one();s.close()
            s=Store(db,'bot',{'room'},100)
            s.ingest(Message('bot','room','s:100','alice',('bot',),'总结我之前聊过什么',150),now=151)
            job=s.claim()
            history=s.history(job,True)
            own=[h for h in history if h['kind']=='current_person_history']
            self.assertGreaterEqual(len(own),32)
            self.assertEqual(sum(h['continuity_role']=='latest_turn' for h in own),8)
            self.assertNotIn('其他成员',json.dumps(history,ensure_ascii=False))
            with patch.object(archive,'ROOT',root):
                result=archive.load(job,{'days':30,'before_id':0})
                self.assertEqual(result['matched_remaining'],40)
                self.assertNotIn('其他成员',json.dumps(result,ensure_ascii=False))
                with self.assertRaises(ValueError):archive.load({**job,'sender':'bob'},{'days':30,'before_id':0})
                # Force bounded pages and verify cursor progress without overlap.
                s.db.execute("UPDATE jobs SET answer=? WHERE sender='alice' AND state='confirmed'",('回答'*1500,))
                first=archive.load(job,{'days':30,'before_id':0})
                self.assertIsNotNone(first['next_before_id'])
                second=archive.load(job,{'days':30,'before_id':first['next_before_id']})
                self.assertLess(second['matched_remaining'],first['matched_remaining'])
                self.assertTrue({r['question'] for r in first['records']}.isdisjoint({r['question'] for r in second['records']}))
                s.forget('room','alice')
                self.assertEqual(archive.load(job,{'days':30,'before_id':0})['status'],'unavailable')
            s.close()

if __name__=='__main__':unittest.main()
