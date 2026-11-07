"""Read-only bridge; scope comes from the current real job, never model arguments."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
from group_summary import scrub

ROOT=Path(__file__).resolve().parent


def validate(args):
    return (isinstance(args,dict) and set(args)=={'member','days'} and isinstance(args['member'],str)
            and 1<=len(args['member'])<=80 and type(args['days']) is int and 1<=args['days']<=7)


def load(job,args):
    if (not validate(args) or not isinstance(job,dict) or not str(job.get('message_id','')).startswith('s:')
            or not all(job.get(k) for k in ('account','room','sender','created'))):
        raise ValueError('invalid_member_request')
    request={**args,'account':job['account'],'group':job['room'],'sender':job['sender'],
             'message_id':job['message_id'],'end':int(job['created'])}
    run=subprocess.run(['wsl','-d','WeChatAgent','-u','root','--exec','python3',
        '/opt/wechat-agent-v2/member_window.py'],input=json.dumps(request),capture_output=True,
        encoding='utf8',timeout=18,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    if run.returncode or len(run.stdout)>120000:
        raise ValueError('member_read_unavailable')
    result=json.loads(run.stdout)
    if not isinstance(result,dict):raise ValueError('invalid_member_response')
    target=result.pop('target_id',None)
    if result.get('status')=='ok':
        if not target or not isinstance(result.get('records'),list):raise ValueError('invalid_member_response')
        # Respect existing forget/stop-memory decisions even for public-window summaries.
        db=sqlite3.connect((ROOT/'runtime/state.sqlite3').resolve().as_uri()+'?mode=ro',uri=True,timeout=2)
        try:
            setting=db.execute('SELECT enabled FROM member_settings WHERE account=? AND room=? AND sender=?',
                               (job['account'],job['room'],target)).fetchone()
            if setting and not setting[0]:
                return {'status':'unavailable','message':'该成员已关闭记忆，不提供其历史观点汇总。'}
            # Only attach bot replies to independently resolved, still-visible
            # public source messages in this result; never read facts or private chats.
            used=0;attached=0
            for item in reversed(result['records']):
                ident=item.pop('source_message_id',None)
                if not ident or attached>=12:continue
                row=db.execute("SELECT id,answer FROM jobs WHERE account=? AND room=? AND sender=? AND message_id=? AND state='confirmed' AND created<? ORDER BY id DESC LIMIT 1",
                    (job['account'],job['room'],target,ident,job['created'])).fetchone()
                if not row:continue
                supplement=db.execute('SELECT answer FROM supplements WHERE job_id=? ORDER BY created DESC,receipt_id DESC LIMIT 1',(row[0],)).fetchone()
                answer=scrub((supplement[0] if supplement else row[1]) or '')
                excerpt=answer[:1800]
                if used+len(excerpt)>12000:continue
                item['bot_answer']=excerpt;item['bot_answer_excerpt']=len(answer)>1800
                attached+=1;used+=len(excerpt)
            for item in result['records']:item.pop('source_message_id',None)
            if 'coverage' in result:result['coverage']=result['coverage'].replace(', bot answers','')
            result['bot_answer_count']=attached
            result['bot_answer_coverage']='Up to12 confirmed bot replies to the matched public questions; bot answers are NOT member views. Other answers may be omitted.'
        finally:db.close()
    return result


TOOL={'type':'namespace','name':'group_views','description':'Owner-authorized summaries of a specified member public statements in THIS group only; not private personal memory.',
 'tools':[{'type':'function','name':'read_member_statements',
 'description':'Only for an explicit request to summarize a member recent views/discussion in this group. member: exact account nickname, @mentioned for a real WeChat mention, @quoted for the author of the explicitly quoted message, @self for asker. No roster listing or fuzzy guesses. days=1..7 (default3 when recent is unspecified). Not for private identity, credentials, personal profiles or private chat. Return text/date/coverage, not external fact verification.',
 'inputSchema':{'type':'object','properties':{'member':{'type':'string'},'days':{'type':'integer','minimum':1,'maximum':7}},'required':['member','days'],'additionalProperties':False}}]}
