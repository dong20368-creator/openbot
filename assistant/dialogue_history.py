"""Read the asker's own durable confirmed bot dialogue; no cross-person lookup."""
import json,sqlite3,time
from pathlib import Path
from group_summary import scrub
from context_budget import excerpt_text
ROOT=Path(__file__).resolve().parent

def load(job,args):
    if (not isinstance(args,dict) or set(args)!={'days','before_id'}
        or type(args['days']) is not int or not 1<=args['days']<=3650
        or type(args['before_id']) is not int or args['before_id']<0):
        raise ValueError('invalid_dialogue_request')
    db=sqlite3.connect((ROOT/'runtime/state.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    try:
        actual=db.execute('SELECT * FROM jobs WHERE id=?',(job['id'],)).fetchone()
        if not actual or any(actual[k]!=job[k] for k in ('account','room','sender','message_id','created')):
            raise ValueError('dialogue_scope_mismatch')
        setting=db.execute('SELECT enabled,epoch FROM member_settings WHERE account=? AND room=? AND sender=?',
            (job['account'],job['room'],job['sender'])).fetchone()
        if setting and (not setting['enabled'] or setting['epoch']!=job.get('context_epoch',0)):
            return {'status':'unavailable','notice':'当前成员已关闭或重置记忆，不读取旧记录。'}
        before=min(args['before_id'] or job['id'],job['id'])
        scope=(job['account'],job['room'],job['sender'],before,job['created']-args['days']*86400,job['created'])
        sql="FROM jobs WHERE account=? AND room=? AND sender=? AND id<? AND created>=? AND created<? AND state='confirmed' AND question!=''"
        total=db.execute('SELECT COUNT(*) '+sql,scope).fetchone()[0]
        rows=db.execute('SELECT * '+sql+' ORDER BY id DESC LIMIT 100',scope).fetchall()
        records=[];used=0;last=before
        for row in rows:
            answer=db.execute('SELECT answer FROM supplements WHERE job_id=? ORDER BY created DESC,receipt_id DESC LIMIT 1',(row['id'],)).fetchone()
            q=scrub(row['question']);a=scrub(answer[0] if answer else row['answer'] or '')
            item={'created':row['created'],'question':excerpt_text(q,job.get('question',''),2000),
                  'bot_answer':excerpt_text(a,job.get('question',''),3000),
                  'excerpt':len(q)>2000 or len(a)>3000}
            cost=len(json.dumps(item,ensure_ascii=False))
            if records and used+cost>24000:break
            records.append(item);used+=cost;last=row['id']
        return {'status':'ok','kind':'current_person_dialogue_archive','records':list(reversed(records)),
            'matched_remaining':total,'returned':len(records),'next_before_id':last if total>len(records) else None,
            'coverage':'Only this asker confirmed bot Q&A in this group; replies are bot statements, not member views. Private chats and other members excluded. Sensitive lines redacted; excerpts labelled; pagination may be needed.'}
    finally:db.close()

TOOL={'type':'namespace','name':'own_dialogue','description':'Read only the current asker persistent bot conversations for recall or summaries.',
 'tools':[{'type':'function','name':'read','description':'For summarizing or recalling MY prior conversations with the bot. Never use for another person. days 1..3650; default30; all recorded history use3650. before_id=0 initially, then next_before_id for older pages. Report coverage and omissions.',
 'inputSchema':{'type':'object','properties':{'days':{'type':'integer','minimum':1,'maximum':3650},'before_id':{'type':'integer','minimum':0}},'required':['days','before_id'],'additionalProperties':False}}]}
