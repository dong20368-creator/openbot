import json
import unittest
from core import Store,Message


class ConversationFloor(unittest.TestCase):
    def setUp(self):
        self.s=Store(':memory:','bot',{'room','second'},0)
    def tearDown(self):self.s.close()
    def add(self,ident,sender='other',room='room',delivered=200,answer='公开评价',state='confirmed'):
        self.s.ingest(Message('bot',room,str(ident),sender,('bot',),'不应共享的原问题',100),now=100)
        jid=self.s.db.execute('SELECT id FROM jobs WHERE message_id=?',(str(ident),)).fetchone()[0]
        self.s.db.execute('UPDATE jobs SET state=?,answer=? WHERE id=?',(state,answer,jid))
        self.s.db.execute('INSERT INTO job_events(job_id,event,created,metadata) VALUES(?,?,?,?)',(jid,'confirmed',delivered,'{}'))
    def history(self,**changes):
        job=dict(id=999,account='bot',room='room',sender='current',created=400,question='评价更尖锐一点',reference_id=None)
        job.update(changes)
        return self.s.history(job,True)
    def test_public_answer_only_not_private_question_or_facts(self):
        self.add(1)
        self.s.db.execute("INSERT INTO facts VALUES('bot','room','other','secret','private','1',200)")
        result=self.history()
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['kind'],'recent_public_bot_reply')
        text=json.dumps(result,ensure_ascii=False)
        for forbidden in ('原问题','private','secret','message_id','"sender"'):self.assertNotIn(forbidden,text)
    def test_scope_time_delivery_and_limit(self):
        self.add(1,room='second');self.add(2,delivered=99);self.add(3,delivered=401)
        self.add(4,state='uncertain')
        self.assertEqual(self.history(),[])
        for i in range(5,10):self.add(i,delivered=200+i)
        self.assertEqual([x['created'] for x in self.history()],[207,208,209])
    def test_reference_and_disabled_member_do_not_expand(self):
        self.add(1)
        self.assertFalse(any(x['kind']=='recent_public_bot_reply' for x in self.history(reference_id='unknown')))
        self.s.db.execute("INSERT INTO member_settings VALUES('bot','room','other',0,1)")
        self.assertEqual(self.history(),[])
    def test_sensitive_lines_removed_and_forgetting_respected(self):
        self.add(1,answer='密码不应共享\n公开评价')
        self.assertEqual(self.history()[0]['answer'],'公开评价')
        self.s.db.execute("UPDATE jobs SET question='',answer=NULL")
        self.assertEqual(self.history(),[])

if __name__=='__main__':unittest.main()
