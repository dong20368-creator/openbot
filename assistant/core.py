"""Durable message processing, independent of the desktop and model provider.

Only normalized, explicitly mentioned messages enter this store. An adapter must
supply verified account/group/message identities; display names are never keys.
"""
from dataclasses import dataclass
from answer_limits import MAX_REPLY_CHARS, MAX_MEMORIES
import hashlib
import re
import sqlite3
import time
import json
import math
import random
from context_budget import search_terms,bounded_history
from group_summary import is_summary
from outbound_images import attachments,receipt_matches

def model_error_code(exc):
    known={'codex_process_ended','codex_timeout','codex_empty_answer','codex_turn_failed','chatgpt_login_required'}
    known.update('codex_rpc_failed:'+method for method in ('initialize','account/read','thread/start','turn/start'))
    value=str(exc)
    return value if value in known else 'unclassified'


@dataclass(frozen=True)
class Message:
    account: str
    group: str
    message_id: str
    sender: str
    mentions: tuple[str, ...]
    text: str
    created: float
    reference_id: str | None = None
    reference_data: dict | None = None


class Store:
    def __init__(self, path, account, groups, activated_at, max_queue_age=None):
        if not account or not groups:
            raise ValueError('Account and explicit group whitelist required')
        self.account, self.groups = account, frozenset(groups)
        self.activated_at = activated_at
        self.max_queue_age=max_queue_age
        self.semantic=None
        self.db = sqlite3.connect(path, isolation_level=None,timeout=20)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('PRAGMA secure_delete=ON')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS jobs(
                id INTEGER PRIMARY KEY, account TEXT NOT NULL, room TEXT NOT NULL,
                message_id TEXT NOT NULL, sender TEXT NOT NULL, question TEXT NOT NULL,
                created REAL NOT NULL, reference_id TEXT, state TEXT NOT NULL,
                answer TEXT, error_code TEXT, receipt_id TEXT,
                UNIQUE(account,room,message_id));
            CREATE INDEX IF NOT EXISTS pending_jobs ON jobs(state,created,id);
            CREATE INDEX IF NOT EXISTS person_history ON jobs(account,room,sender,state,id);
            CREATE TABLE IF NOT EXISTS generation_turns(
                account TEXT,room TEXT,sender TEXT,last_started REAL NOT NULL,
                PRIMARY KEY(account,room,sender));
            CREATE TABLE IF NOT EXISTS facts(
                account TEXT, room TEXT, sender TEXT, fact_key TEXT, value TEXT,
                source_message_id TEXT NOT NULL, updated REAL NOT NULL,
                PRIMARY KEY(account,room,sender,fact_key));
            CREATE TABLE IF NOT EXISTS proposed_facts(
                job_id INTEGER NOT NULL, fact_key TEXT NOT NULL, value TEXT NOT NULL,
                evidence TEXT NOT NULL, PRIMARY KEY(job_id,fact_key));
            CREATE TABLE IF NOT EXISTS member_settings(
                account TEXT,room TEXT,sender TEXT,enabled INTEGER NOT NULL,epoch INTEGER NOT NULL,
                PRIMARY KEY(account,room,sender));
            CREATE TABLE IF NOT EXISTS job_events(
                id INTEGER PRIMARY KEY,job_id INTEGER,event TEXT,created REAL,metadata TEXT);
            CREATE TABLE IF NOT EXISTS model_audits(
                job_id INTEGER PRIMARY KEY,created REAL,metadata TEXT);
            CREATE TABLE IF NOT EXISTS supplements(
                job_id INTEGER NOT NULL,receipt_id TEXT UNIQUE NOT NULL,
                answer TEXT NOT NULL,answer_hash TEXT NOT NULL,created REAL NOT NULL,
                reference_id TEXT NOT NULL,PRIMARY KEY(job_id,receipt_id));
            CREATE TABLE IF NOT EXISTS semantic_chunks(
                job_id INTEGER,chunk INTEGER,content_hash TEXT,model TEXT,embedding BLOB,
                PRIMARY KEY(job_id,chunk));
            CREATE TABLE IF NOT EXISTS fact_events(
                account TEXT,room TEXT,sender TEXT,fact_key TEXT,value TEXT,
                source_message_id TEXT,valid_from REAL,
                PRIMARY KEY(account,room,sender,fact_key,source_message_id));
        ''')
        columns={r[1] for r in self.db.execute('PRAGMA table_info(jobs)')}
        for name,definition in {'attempts':'INTEGER NOT NULL DEFAULT 0','next_attempt':'REAL NOT NULL DEFAULT 0',
            'send_started':'REAL','answer_hash':'TEXT','context_epoch':'INTEGER NOT NULL DEFAULT 0',
            'mention_ids':"TEXT NOT NULL DEFAULT '[]'",'reference_data':'TEXT','attachments':"TEXT NOT NULL DEFAULT '[]'"}.items():
            if name not in columns: self.db.execute(f'ALTER TABLE jobs ADD COLUMN {name} {definition}')
        exists=self.db.execute("SELECT 1 FROM sqlite_master WHERE name='job_search'").fetchone()
        self.db.execute('CREATE VIRTUAL TABLE IF NOT EXISTS job_search USING fts5(terms)')
        if not exists:
            for row in self.db.execute("SELECT * FROM jobs WHERE state='confirmed' AND question!=''").fetchall():
                self.index_job(dict(row))
        for row in self.db.execute("SELECT id,answer FROM jobs WHERE answer IS NOT NULL AND answer_hash IS NULL").fetchall():
            self.db.execute('UPDATE jobs SET answer_hash=? WHERE id=?',(hashlib.sha256(row['answer'].encode()).hexdigest(),row['id']))
        self.db.execute('CREATE UNIQUE INDEX IF NOT EXISTS receipt_once ON jobs(account,room,receipt_id) WHERE receipt_id IS NOT NULL')

    def scope(self):
        return 'account=? AND room IN ('+','.join('?' for _ in self.groups)+')',(self.account,*sorted(self.groups))

    def event(self,job_id,event,**metadata):
        self.db.execute('INSERT INTO job_events(job_id,event,created,metadata) VALUES(?,?,?,?)',
            (job_id,event,time.time(),json.dumps(metadata)))

    def audit(self,job_id,data):
        safe={k:data[k] for k in ('web_events','observed_urls','category','evidence_passed','elapsed_seconds','memory_searches','evidence_repair_attempted','evidence_check',
            'member_view_calls','member_view_records','summary_read_errors','dialogue_reads','incoming_image_count','incoming_media_dimensions','incoming_media_errors','futu_reads','public_research_calls','x_post_batch_calls','public_research_reads','public_feed_reads','image_reads','prepared_image_count','primary_page_reads','math_calls','math_tool_rounds','evidence_grounding_attempted','queue_wait_seconds','connection_setup_seconds','connection_reused','phase_seconds','repair_reasons','revision_failed','source_prefetch_seconds','tool_seconds') if k in data}
        self.db.execute('INSERT OR REPLACE INTO model_audits VALUES(?,?,?)',(job_id,time.time(),json.dumps(safe)))

    def index_job(self,job):
        self.db.execute('DELETE FROM job_search WHERE rowid=?',(job['id'],))
        if job['question']:
            self.db.execute('INSERT INTO job_search(rowid,terms) VALUES(?,?)',
                (job['id'],' '.join(search_terms(job['question']+' '+self.context_answer(job),limit=4096))))

    def context_answer(self,job):
        if job['account']!=self.account or job['room'] not in self.groups:
            raise ValueError('Context scope mismatch')
        row=self.db.execute('SELECT answer FROM supplements WHERE job_id=? ORDER BY created DESC,receipt_id DESC LIMIT 1',(job['id'],)).fetchone()
        return row['answer'] if row else (job['answer'] or '')

    def record_supplement(self,job_id,answer,receipt,created=None):
        """Caller supplies an independently verified receipt, never a click result."""
        if not isinstance(answer,str) or not answer.strip() or len(answer)>MAX_REPLY_CHARS:
            raise ValueError('Invalid supplement')
        digest=hashlib.sha256(answer.encode()).hexdigest()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            scope,args=self.scope()
            row=self.db.execute(f"SELECT * FROM jobs WHERE id=? AND state='confirmed' AND {scope}",(job_id,*args)).fetchone()
            if not row or not row['question'] or not self.memory_setting(row['room'],row['sender'])[0]:
                raise ValueError('Supplement history unavailable or forgotten')
            if (receipt.get('account')!=row['account'] or receipt.get('group')!=row['room']
                or receipt.get('reference_id')!=row['message_id'] or receipt.get('text_sha256')!=digest
                or not receipt.get('message_id')):raise ValueError('Supplement receipt mismatch')
            if self.db.execute('SELECT 1 FROM jobs WHERE receipt_id=?',(receipt['message_id'],)).fetchone():
                raise ValueError('Receipt already belongs to an original reply')
            prior=self.db.execute('SELECT job_id,answer_hash FROM supplements WHERE receipt_id=?',(receipt['message_id'],)).fetchone()
            if prior and (prior['job_id']!=job_id or prior['answer_hash']!=digest):raise ValueError('Receipt already used')
            if not prior:
                self.db.execute('INSERT INTO supplements VALUES(?,?,?,?,?,?)',
                    (job_id,receipt['message_id'],answer,digest,time.time() if created is None else created,receipt['reference_id']))
                self.index_job(dict(row))
                self.db.execute('DELETE FROM semantic_chunks WHERE job_id=?',(job_id,))
                self.event(job_id,'supplement_in_context',answer_hash=digest)
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK');raise

    def memory_setting(self,room,sender):
        row=self.db.execute('SELECT enabled,epoch FROM member_settings WHERE account=? AND room=? AND sender=?',
            (self.account,room,sender)).fetchone()
        return (bool(row['enabled']),row['epoch']) if row else (True,0)

    def close(self):
        self.db.close()

    def ingest(self, msg, now=None):
        now = time.time() if now is None else now
        if (not all(isinstance(v,str) for v in (msg.account,msg.group,msg.message_id,msg.sender,msg.text))
            or not isinstance(msg.mentions,(tuple,list)) or not all(isinstance(v,str) for v in msg.mentions)
            or not isinstance(msg.created,(int,float)) or not math.isfinite(msg.created)):
            return False
        if (msg.account != self.account or msg.group not in self.groups
                or self.account not in msg.mentions or msg.sender == self.account
                or not msg.message_id or not msg.sender or not msg.text.strip()
                or len(msg.text) > 8000 or msg.created < self.activated_at
                or msg.created > now + 30 or now - msg.created > (self.max_queue_age or 600)):
            return False
        reference=msg.reference_data
        if reference:
            if not isinstance(reference,dict) or reference.get('message_id')!=msg.reference_id: return False
            chain=reference.get('chain',[])
            if not isinstance(chain,list) or len(chain)>2 or len(json.dumps(reference))>40000: return False
            for source in [reference,*chain]:
                if (not isinstance(source,dict) or source.get('account')!=self.account
                    or source.get('group')!=msg.group or not isinstance(source.get('sender'),str)
                    or not source['sender'] or not isinstance(source.get('message_id'),str)
                    or not isinstance(source.get('text'),str)
                    or not isinstance(source.get('created'),(int,float)) or not math.isfinite(source['created'])): return False
        result = self.db.execute('''INSERT OR IGNORE INTO jobs
            (account,room,message_id,sender,question,created,reference_id,mention_ids,reference_data,state)
            VALUES(?,?,?,?,?,?,?,?,?,'pending')''', (msg.account,msg.group,msg.message_id,
            msg.sender,msg.text,msg.created,msg.reference_id,json.dumps(list(msg.mentions)),
            json.dumps(reference,ensure_ascii=False) if reference else None))
        return result.rowcount == 1

    def recover(self):
        # Only call while holding the single-worker OS lock. Generating has no
        # external side effect; sending may already have reached WeChat.
        scope,args=self.scope()
        self.db.execute(f"UPDATE jobs SET state='pending' WHERE state='generating' AND {scope}",args)
        self.db.execute(f"UPDATE jobs SET state='ready' WHERE state='dispatching' AND {scope}",args)
        self.db.execute(f"UPDATE jobs SET state='uncertain',error_code='restart_during_send' WHERE state='sending' AND {scope}",args)
        for row in self.db.execute(f'''SELECT DISTINCT j.* FROM jobs j JOIN proposed_facts p
            ON p.job_id=j.id WHERE {scope} AND j.state='confirmed' ''',args).fetchall():
            self.commit_facts(dict(row))

    def delivery_blocked(self,independent_intents=False):
        scope,args=self.scope()
        states="('attention')" if independent_intents else "('uncertain','attention')"
        return bool(self.db.execute(f"SELECT 1 FROM jobs WHERE {scope} AND state IN {states} LIMIT 1",args).fetchone())

    def expired(self,job,now=None):
        now=time.time() if now is None else now
        if job['sender']=='__scheduled_group_summary__' and re.fullmatch(r'summary:\d+',job['message_id']):
            return now>int(job['message_id'].split(':')[1])+3600
        return bool(self.max_queue_age and job['created']<now-self.max_queue_age)

    def claim(self,now=None,stage='all'):
        states={'all':"'pending','retry','ready'",'generate':"'pending','retry'",'deliver':"'ready'"}
        if stage not in states:raise ValueError('Invalid processing stage')
        now=time.time() if now is None else now
        scope,args=self.scope()
        ordering='created,id'
        if stage=='generate':
            ordering='''COALESCE((SELECT last_started FROM generation_turns t WHERE
                t.account=jobs.account AND t.room=jobs.room AND t.sender=jobs.sender),jobs.created),created,id'''
        self.db.execute('BEGIN IMMEDIATE')
        try:
            for queued in self.db.execute(f"SELECT * FROM jobs WHERE state IN ('pending','retry','ready') AND {scope}",args).fetchall():
                if self.expired(queued,now):
                    self.db.execute("UPDATE jobs SET state='expired',error_code='queue_expired' WHERE id=?",(queued['id'],))
            row = self.db.execute(f'''SELECT * FROM jobs WHERE state IN ({states[stage]}) AND next_attempt<=? AND {scope}
                AND NOT EXISTS (SELECT 1 FROM jobs older WHERE older.account=jobs.account AND older.room=jobs.room
                    AND older.sender=jobs.sender AND older.id<jobs.id AND
                    (older.state IN ('pending','retry','ready','generating','dispatching','sending') OR
                     (older.state='confirmed' AND EXISTS (SELECT 1 FROM proposed_facts p WHERE p.job_id=older.id))))
                ORDER BY {ordering} LIMIT 1''', (now,*args)).fetchone()
            if row:
                epoch=row['context_epoch'] if row['state']=='ready' else self.memory_setting(row['room'],row['sender'])[1]
                state='dispatching' if row['state']=='ready' else 'generating'
                self.db.execute('UPDATE jobs SET state=?,attempts=attempts+?,context_epoch=? WHERE id=?',
                    (state,int(state=='generating'),epoch,row['id']))
                if stage=='generate':
                    self.db.execute('''INSERT INTO generation_turns VALUES(?,?,?,?)
                        ON CONFLICT(account,room,sender) DO UPDATE SET last_started=excluded.last_started''',
                        (row['account'],row['room'],row['sender'],now))
            self.db.execute('COMMIT')
            if not row: return None
            result=dict(row); result['context_epoch']=epoch
            if state=='generating': result['attempts']+=1
            self.event(row['id'],state,attempt=result['attempts'])
            return result
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def transition(self, job_id, expected, state, **fields):
        if set(fields) - {'answer','error_code','receipt_id','next_attempt','send_started','answer_hash'}:
            raise ValueError('Unsupported state field')
        sets = ','.join(['state=?'] + [key+'=?' for key in fields])
        scope,args=self.scope()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            cursor = self.db.execute(f'UPDATE jobs SET {sets} WHERE id=? AND state=? AND {scope}',
                                     (state,*fields.values(),job_id,expected,*args))
            if cursor.rowcount != 1: raise RuntimeError('Unexpected job state; no operation performed')
            self.event(job_id,state,error_code=fields.get('error_code'))
            if state=='confirmed':
                self.index_job(dict(self.db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()))
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK'); raise

    def history(self, job, enabled=False):
        if not enabled:
            return []
        if not self.memory_setting(job['room'],job['sender'])[0]: return []
        if job['account'] != self.account or job['room'] not in self.groups:
            raise ValueError('Context scope mismatch')
        # No half-hour expiry: keep continuity across sessions. Selection is
        # bounded, but history remains available on disk until explicitly erased.
        scope = (self.account,job['room'],job['sender'],job['id'])
        rows = self.db.execute('''SELECT * FROM jobs WHERE
            account=? AND room=? AND sender=? AND state='confirmed'
            AND id<? AND question!='' ORDER BY id DESC LIMIT 32''', scope).fetchall()
        selected = {r['id']: r for r in rows}
        recent_ids={r['id'] for r in rows[:8]}
        if self.semantic:
            try: selected.update({r['id']:r for r in self.semantic(job)})
            except Exception as exc: self.event(job['id'],'semantic_fallback',error_type=type(exc).__name__)
        # Parameterized literal fragments also work for Chinese, unlike the
        # default SQLite word tokenizer. This retrieval never crosses a person.
        terms=search_terms(job['question'])[:32]
        if terms:
            query=' OR '.join('"'+term+'"' for term in terms)
            older = self.db.execute('''SELECT j.* FROM job_search JOIN jobs j ON j.id=job_search.rowid
                WHERE job_search MATCH ? AND account=? AND room=? AND sender=? AND state='confirmed'
                AND j.id<? AND question!='' ORDER BY bm25(job_search),j.id DESC LIMIT 12''', (query,*scope)).fetchall()
            selected.update({r['id']:r for r in older})
        def entry(row,kind):
            return {'kind':kind,'speaker':hashlib.sha256(
                (self.account+'\0'+row['room']+'\0'+row['sender']).encode()).hexdigest()[:24],
                'message_id':row['message_id'],'created':row['created'],
                'question':row['question'],'answer':self.context_answer(row),
                'continuity_role':'latest_turn' if kind=='current_person_history' and row['id'] in recent_ids else 'older_turn'}
        result=[]
        for row in sorted(selected.values(),key=lambda r:r['id'],reverse=True):
            # Budgeting below retains excerpts; do not discard whole recent
            # turns here before the continuity-aware selector can see them.
            result.append(entry(row,'current_person_history'))
        result.reverse()
        # Privacy: mentioning someone is not permission to retrieve their stored
        # personal history. Only the current person's records are recalled.
        # An explicit reference can cross a sender, but never an account/room.
        # Receipt IDs allow a user to quote the bot's answer as well as a question.
        if job.get('reference_id'):
            ref=self.db.execute('''SELECT * FROM jobs WHERE account=? AND room=?
                AND (message_id=? OR receipt_id=? OR id IN (SELECT job_id FROM supplements WHERE receipt_id=?)) AND id<? AND state='confirmed'
                AND question!='' ORDER BY id DESC LIMIT 1''',
                (self.account,job['room'],job['reference_id'],job['reference_id'],job['reference_id'],job['id'])).fetchone()
            if ref and ref['sender']!=job['sender']: ref=None
            if ref:
                result.append(entry(ref,'explicit_reference'))
            elif job.get('reference_data'):
                supplied=json.loads(job['reference_data'])
                if supplied.get('account')==self.account and supplied.get('group')==job['room']:
                    for source in [supplied,*supplied.get('chain',[])][:3]:
                        if source.get('account')!=self.account or source.get('group')!=job['room']: continue
                        result.append({'kind':'explicit_reference','message_id':source['message_id'],
                            'created':source['created'],'speaker':hashlib.sha256((self.account+'\0'+job['room']+'\0'+source['sender']).encode()).hexdigest()[:24],
                            'speaker_type':'assistant' if source['sender']==self.account else 'member',
                            'question':source['text'],'answer':None,'excerpt':source.get('excerpt',False)})
            else: result.append({'kind':'unresolved_reference','message_id':job['reference_id']})
        facts=self.db.execute('''SELECT fact_key,value,source_message_id,updated FROM facts
            WHERE account=? AND room=? AND sender=? AND updated>=? ORDER BY updated DESC LIMIT 32''',
            (self.account,job['room'],job['sender'],job['created']-180*86400)).fetchall()
        if facts:
            result.insert(0,{'kind':'current_person_explicit_facts','facts':[dict(f) for f in facts]})
        revisions=self.db.execute('''SELECT fact_key,value,source_message_id,valid_from FROM fact_events
            WHERE account=? AND room=? AND sender=? ORDER BY valid_from DESC LIMIT 20''',
            (self.account,job['room'],job['sender'])).fetchall()
        if revisions:
            result.append({'kind':'current_person_fact_timeline','created':job['created'],'notice':'个人明确事实的历史版本，旧值不代表现状。',
                'events':[dict(r) for r in reversed(revisions)]})
        return bounded_history(job['question'],result)

    def propose_facts(self,job,proposals):
        # Literal evidence must be in THIS person's current message, not a
        # quoted person's text or a model conclusion. Applied only after receipt.
        enabled,epoch=self.memory_setting(job['room'],job['sender'])
        if job.get('reference_id') or not enabled or job.get('context_epoch',0)!=epoch:
            return
        for item in proposals[:MAX_MEMORIES]:
            key,value,evidence=item.get('key',''),item.get('value',''),item.get('evidence','')
            if (not all(isinstance(x,str) for x in (key,value,evidence))
                or not 1<=len(key)<=80 or not 1<=len(value)<=1000
                or not 2<=len(evidence)<=500 or evidence not in job['question']):
                continue
            self.db.execute('INSERT OR REPLACE INTO proposed_facts VALUES(?,?,?,?)',
                (job['id'],key,value,evidence))

    def commit_facts(self,job):
        scope,args=self.scope()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            source=self.db.execute(f"SELECT * FROM jobs WHERE id=? AND state='confirmed' AND {scope}",(job['id'],*args)).fetchone()
            if not source: raise ValueError('Memory requires confirmed scoped job')
            self._commit_facts(dict(source))
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK'); raise

    def _commit_facts(self,job):
        enabled,epoch=self.memory_setting(job['room'],job['sender'])
        if not enabled or job.get('context_epoch',0)!=epoch:
            self.db.execute('DELETE FROM proposed_facts WHERE job_id=?',(job['id'],))
            self.db.execute("UPDATE jobs SET question='',answer=NULL,reference_data=NULL WHERE id=? AND state='confirmed'",(job['id'],))
            self.db.execute('DELETE FROM job_search WHERE rowid=?',(job['id'],))
            self.db.execute('DELETE FROM semantic_chunks WHERE job_id=?',(job['id'],))
            return
        rows=self.db.execute('SELECT fact_key,value FROM proposed_facts WHERE job_id=?',(job['id'],)).fetchall()
        for row in rows: self.remember(job,row['fact_key'],row['value'])
        self.db.execute('DELETE FROM proposed_facts WHERE job_id=?',(job['id'],))

    def remember(self, job, key, value):
        source = self.db.execute('SELECT * FROM jobs WHERE id=? AND account=?',
                                 (job['id'],self.account)).fetchone()
        if not source or source['state'] != 'confirmed':
            raise ValueError('Memory requires a confirmed source')
        if not key or len(key)>80 or not value or len(value)>1000:
            raise ValueError('Invalid memory size')
        self.db.execute('INSERT OR IGNORE INTO fact_events VALUES(?,?,?,?,?,?,?)',
            (self.account,source['room'],source['sender'],key,value,source['message_id'],source['created']))
        self.db.execute('''INSERT INTO facts VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(account,room,sender,fact_key) DO UPDATE SET
            value=excluded.value,source_message_id=excluded.source_message_id,updated=excluded.updated
            WHERE excluded.updated>=facts.updated''',
            (self.account,source['room'],source['sender'],key,value,source['message_id'],source['created']))

    def forget(self, room, sender):
        # Delete only assistant-owned content, never original WeChat databases.
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.db.execute('''INSERT INTO member_settings VALUES(?,?,?,0,1)
                ON CONFLICT(account,room,sender) DO UPDATE SET enabled=0,epoch=member_settings.epoch+1''',
                (self.account,room,sender))
            self.db.execute('''DELETE FROM job_search WHERE rowid IN
                (SELECT id FROM jobs WHERE account=? AND room=? AND sender=?)''',(self.account,room,sender))
            self.db.execute('DELETE FROM facts WHERE account=? AND room=? AND sender=?', (self.account,room,sender))
            self.db.execute('DELETE FROM generation_turns WHERE account=? AND room=? AND sender=?',(self.account,room,sender))
            self.db.execute('DELETE FROM fact_events WHERE account=? AND room=? AND sender=?',(self.account,room,sender))
            self.db.execute('DELETE FROM semantic_chunks WHERE job_id IN (SELECT id FROM jobs WHERE account=? AND room=? AND sender=?)',(self.account,room,sender))
            self.db.execute('DELETE FROM supplements WHERE job_id IN (SELECT id FROM jobs WHERE account=? AND room=? AND sender=?)',(self.account,room,sender))
            self.db.execute('''DELETE FROM proposed_facts WHERE job_id IN
                (SELECT id FROM jobs WHERE account=? AND room=? AND sender=?)''',(self.account,room,sender))
            self.db.execute("UPDATE jobs SET question='',answer=NULL,reference_data=NULL WHERE account=? AND room=? AND sender=? AND state IN ('confirmed','failed','preview','uncertain','attention','expired','cancelled')", (self.account,room,sender))
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def status(self):
        scope,args=self.scope()
        return dict(self.db.execute(f'SELECT state,count(*) FROM jobs WHERE {scope} GROUP BY state',args))

    def memory_control(self,job):
        if job.get('reference_id'): return None
        text=re.sub(r'^(?:\s*@\S+[\s\u2005]+)+','',job['question']).strip().rstrip('。！!')
        room,sender=job['room'],job['sender']
        if text in ('忘记我','删除我的记忆','不要记住我','停止记录我'):
            self.forget(room,sender)
            return '已清除我在本群保存的你的问答记忆，并停止记忆；其他人也不能再通过我汇总你的历史观点。微信原始聊天不会被删除，已授权的群聊整体摘要仍按原范围处理。想重新开启，可以说“恢复记忆”。'
        if text in ('恢复记忆','可以记住我'):
            self.db.execute('''INSERT INTO member_settings VALUES(?,?,?,1,1)
                ON CONFLICT(account,room,sender) DO UPDATE SET enabled=1,epoch=member_settings.epoch+1''',
                (self.account,room,sender))
            return '已恢复你在本群的记忆，从现在开始保存新的问答，不恢复已清除的记忆；本群公开观点汇总也恢复可用。'
        if text=='查看我的记忆':
            if not self.memory_setting(room,sender)[0]: return '你在本群的记忆已关闭，也不提供你的历史观点汇总。微信原始聊天未删除，群聊整体摘要不受此开关影响。'
            count=self.db.execute('SELECT count(*) FROM facts WHERE account=? AND room=? AND sender=?',
                (self.account,room,sender)).fetchone()[0]
            return f'你的记忆已开启，共保存{count}项明确信息。为保护隐私，我不会在群里列出个人记忆明细。可以对我说“忘记我”清除并停用。'
        return None


class NativeDeliveryRejected(RuntimeError):
    """An authoritative terminal refusal, not an uncertain submission."""
    def __init__(self,code):
        super().__init__(code)
        self.code=code


class DeliveryNotAttempted(RuntimeError):
    """A verified failure before any send action. Never use after clicking Send."""
    def __init__(self,message,retryable=False,code='safe_pre_send_failure'):
        super().__init__(message)
        self.retryable=retryable
        self.code=code


class SourceRevoked(DeliveryNotAttempted):
    def __init__(self):
        super().__init__('Source message revoked',code='source_revoked')


class Worker:
    def __init__(self, store, model, sender, mode='preview', context_enabled=True,stage='all'):
        if mode not in ('preview','send'):
            raise ValueError('Invalid mode')
        if stage not in ('all','generate','deliver') or (mode=='preview' and stage!='all'):
            raise ValueError('Invalid processing stage')
        self.store,self.model,self.sender = store,model,sender
        self.mode,self.context_enabled = mode,context_enabled
        self.stage=stage

    def run_one(self):
        if self.mode=='send' and self.store.delivery_blocked(getattr(self.sender,'independent_intents',False)): return False
        job = self.store.claim(stage=self.stage)
        if job is None:
            return False
        phase='dispatching' if job['state']=='ready' else 'generating'
        if hasattr(self.sender,'source_info') and not is_summary(job):
            try:
                if self.sender.source_info(job).get('source_state')=='revoked':
                    self.store.transition(job['id'],phase,'cancelled',error_code='source_revoked')
                    self.store.db.execute('DELETE FROM proposed_facts WHERE job_id=?',(job['id'],))
                    return True
            except Exception as exc:
                # A failed read is not evidence of withdrawal. Do not generate
                # or send while the source cannot be checked.
                self.store.event(job['id'],'source_check_error',error_type=type(exc).__name__)
                count=self.store.db.execute("SELECT count(*) FROM job_events WHERE job_id=? AND event='source_check_error'",(job['id'],)).fetchone()[0]
                self.store.transition(job['id'],phase,'failed' if count>=3 else ('ready' if phase=='dispatching' else 'retry'),
                    error_code='source_check_unavailable',next_attempt=time.time()+10)
                return True
        if phase=='generating':
            started=time.monotonic()
            self.store.db.execute("UPDATE jobs SET attachments='[]' WHERE id=?",(job['id'],))
            job['attachments']='[]'
            self.store.db.execute('DELETE FROM proposed_facts WHERE job_id=?',(job['id'],))
            self.store.db.execute('DELETE FROM model_audits WHERE job_id=?',(job['id'],))
            try:
                reply=self.store.memory_control(job)
                if reply is None:
                    if hasattr(self.model,'retriever'):
                        self.model.retriever=lambda query:self.store.history({**job,'question':query},self.context_enabled)
                    if hasattr(self.model,'current_job'): self.model.current_job=job
                    reply=self.model.answer(job['question'],self.store.history(job,self.context_enabled))
                    audit=dict(getattr(self.model,'last_audit',{}))
                    audit['elapsed_seconds']=round(time.monotonic()-started,2)
                    audit['queue_wait_seconds']=round(max(0,time.time()-job['created']-audit['elapsed_seconds']),2)
                else:
                    job['context_epoch']=self.store.memory_setting(job['room'],job['sender'])[1]
                    self.store.db.execute('UPDATE jobs SET context_epoch=? WHERE id=?',(job['context_epoch'],job['id']))
                    audit=None
                if not isinstance(reply,str) or not reply.strip() or len(reply)>MAX_REPLY_CHARS:
                    raise ValueError('Invalid answer')
                if audit is not None:
                    prepared=attachments(getattr(self.model,'last_attachments',[]))
                    job['attachments']=json.dumps(prepared,separators=(',',':'))
                    self.store.db.execute('UPDATE jobs SET attachments=? WHERE id=?',(job['attachments'],job['id']))
                    self.store.propose_facts(job,getattr(self.model,'last_memories',[]))
                    self.store.audit(job['id'],audit)
            except Exception as exc:
                self.store.db.execute("UPDATE jobs SET attachments='[]' WHERE id=?",(job['id'],))
                job['attachments']='[]'
                self.store.event(job['id'],'model_error',error_type=type(exc).__name__,error_code=model_error_code(exc))
                if job['attempts']<3:
                    self.store.transition(job['id'],'generating','retry',error_code='model_retry',
                        next_attempt=time.time()+random.uniform(10*job['attempts'],15*job['attempts']))
                    return True
                reply='这次暂时没能完成回答，自动重试后仍未恢复。请稍后重新 @ 我提问。'
            digest=hashlib.sha256(reply.encode()).hexdigest()
            self.store.transition(job['id'],'generating','ready',answer=reply,answer_hash=digest,error_code=None)
            phase='ready'
            if self.stage=='generate':return True
        else:
            reply=job['answer']
            audit=self.store.db.execute('SELECT created,metadata FROM model_audits WHERE job_id=?',(job['id'],)).fetchone()
            if audit and json.loads(audit['metadata']).get('category')=='verified' and time.time()-audit['created']>120:
                # A delayed outbox must recheck public facts, not deliver stale search.
                self.store.transition(job['id'],phase,'pending',answer=None,answer_hash=None,next_attempt=0)
                self.store.db.execute('DELETE FROM proposed_facts WHERE job_id=?',(job['id'],))
                return True
        if self.mode == 'preview':
            self.store.transition(job['id'],phase,'preview')
            return True
        if self.store.expired(job):
            self.store.transition(job['id'],phase,'expired',error_code='queue_expired')
            return True
        if job['context_epoch']!=self.store.memory_setting(job['room'],job['sender'])[1]:
            self.store.transition(job['id'],phase,'pending',answer=None,answer_hash=None,next_attempt=0,error_code='memory_changed')
            self.store.db.execute('DELETE FROM proposed_facts WHERE job_id=?',(job['id'],))
            return True
        try:
            if not self.sender.verify_target(job['account'],job['room']):
                self.store.transition(job['id'],phase,'ready',error_code='target_unverified',next_attempt=time.time()+10)
                return True
        except Exception:
            self.store.transition(job['id'],phase,'ready',error_code='target_unverified',next_attempt=time.time()+10)
            return True
        self.store.transition(job['id'],phase,'sending',send_started=time.time())
        try:
            if hasattr(self.sender,'current_job'):
                other=self.store.db.execute('''SELECT 1 FROM jobs WHERE account=? AND room=? AND id!=?
                    AND (state IN ('pending','retry','ready','generating','dispatching','sending')
                    OR created>=?) LIMIT 1''',
                    (job['account'],job['room'],job['id'],job['created']-120)).fetchone()
                category=self.store.db.execute('SELECT metadata FROM model_audits WHERE job_id=?',(job['id'],)).fetchone()
                privacy_warning=bool(category and json.loads(category[0]).get('category')=='warning')
                self.sender.current_job={**job,'native_quote_needed':bool(job.get('reference_id') or other),
                    'privacy_warning':privacy_warning}
                if (getattr(self.sender,'native_quotes_enabled',False) and not privacy_warning
                    and not (job['sender']=='__scheduled_group_summary__' and re.fullmatch(r'summary:\d+',job['message_id']))):
                    self.store.event(job['id'],'native_quote_required')
            # Delivery evidence is transport-specific: native requires server
            # acceptance plus profile readback; it is not recipient UI proof.
            if hasattr(self.sender,'checkpoint_sink'):
                self.sender.checkpoint_sink=lambda **data:self.store.event(job['id'],'delivery_checkpoint',**data)
            receipt = self.sender.send_and_verify(job['account'],job['room'],reply)
            if receipt and receipt.get('evidence'):
                self.store.event(job['id'],'delivery_evidence',kind=receipt['evidence'])
            timings=getattr(self.sender,'last_timings',None)
            if timings: self.store.event(job['id'],'delivery_timing',**timings)
            expected_hash = hashlib.sha256(reply.encode()).hexdigest()
            native_required=self.store.db.execute("SELECT 1 FROM job_events WHERE job_id=? AND event='native_quote_required' LIMIT 1",(job['id'],)).fetchone()
            if (not receipt or receipt.get('account') != job['account']
                    or receipt.get('group') != job['room'] or not receipt.get('message_id')
                    or receipt.get('reference_id') not in (None,job['message_id'])
                    or (native_required and receipt.get('reference_id')!=job['message_id'])
                    or receipt.get('text_sha256') != expected_hash or not receipt_matches(job,receipt)):
                raise RuntimeError('Unconfirmed send')
            self.store.transition(job['id'],'sending','confirmed',receipt_id=receipt['message_id'])
        except NativeDeliveryRejected as exc:
            self.store.event(job['id'],'native_delivery_rejected',reason=exc.code)
            self.store.transition(job['id'],'sending','failed',error_code=exc.code)
        except SourceRevoked:
            self.store.transition(job['id'],'sending','cancelled',error_code='source_revoked')
            self.store.db.execute('DELETE FROM proposed_facts WHERE job_id=?',(job['id'],))
        except DeliveryNotAttempted as exc:
            self.store.event(job['id'],'delivery_pre_send_error',reason=exc.code,
                retryable=exc.retryable,quote_status=getattr(self.sender,'last_quote_status','not_requested'),
                phase=getattr(self.sender,'last_phase','unknown'))
            if exc.retryable:
                self.store.event(job['id'],'safe_delivery_retry')
                count=self.store.db.execute("SELECT count(*) FROM job_events WHERE job_id=? AND event='safe_delivery_retry'",(job['id'],)).fetchone()[0]
                self.store.transition(job['id'],'sending','ready' if count<3 else 'failed',
                    error_code='safe_pre_send_failure',next_attempt=time.time()+10)
            else:
                self.store.transition(job['id'],'sending','attention',error_code='draft_or_target_changed')
        except Exception as exc:
            self.store.event(job['id'],'delivery_error',error_type=type(exc).__name__,
                phase=getattr(self.sender,'last_phase','unknown'),reason=getattr(exc,'code','unclassified'))
            self.store.transition(job['id'],'sending','uncertain',error_code='send_unconfirmed')
        else:
            self.store.commit_facts(job)
        return True

    def reconcile(self):
        """Read-only reconciliation. Absence of a receipt NEVER permits resend."""
        # Delivery can be confirmed before a local fact commit encounters a
        # transient database failure. Finish that commit without sending again,
        # so the same member's subsequent question can safely proceed.
        scope,args=self.store.scope()
        for row in self.store.db.execute(f'''SELECT DISTINCT j.* FROM jobs j JOIN proposed_facts p
            ON p.job_id=j.id WHERE {scope} AND j.state='confirmed' ''',args).fetchall():
            self.store.commit_facts(dict(row))
        # attention records are explicitly known to have failed BEFORE send.
        # Only a verified pristine editor can release them; never clear drafts.
        if hasattr(self.sender,'can_resume'):
            scope,args=self.store.scope()
            if not self.store.db.execute(f"SELECT 1 FROM jobs WHERE state='uncertain' AND {scope}",args).fetchone():
                for row in self.store.db.execute(f"SELECT * FROM jobs WHERE state='attention' AND error_code='draft_or_target_changed' AND {scope}",args).fetchall():
                    job=dict(row)
                    if self.resume_known_unsent(job):continue
                    if self.sender.can_resume(job['account'],job['room']):
                        self.store.transition(job['id'],'attention','expired' if self.store.expired(job) else 'ready',
                            error_code='operator_draft_cleared',next_attempt=0)
        if not hasattr(self.sender,'find_receipt'): return
        scope,args=self.store.scope()
        rows=self.store.db.execute(f'''SELECT * FROM jobs WHERE state='uncertain' AND {scope}
            AND answer_hash IS NOT NULL ORDER BY id DESC LIMIT 5''',args).fetchall()
        for row in rows:
            job=dict(row)
            if hasattr(self.sender,'find_job_receipt'):
                receipt=self.sender.find_job_receipt(job)
            else:
                receipt=self.sender.find_receipt(job['account'],job['room'],job['answer_hash'],
                    (job['send_started'] or job['created'])-2)
            if not receipt: continue
            native_required=self.store.db.execute("SELECT 1 FROM job_events WHERE job_id=? AND event='native_quote_required' LIMIT 1",(job['id'],)).fetchone()
            if (receipt.get('account')!=job['account'] or receipt.get('group')!=job['room']
                or receipt.get('reference_id') not in (None,job['message_id'])
                or (native_required and receipt.get('reference_id')!=job['message_id'])
                or receipt.get('text_sha256')!=job['answer_hash'] or not receipt.get('message_id') or not receipt_matches(job,receipt)): continue
            duplicate=self.store.db.execute('SELECT 1 FROM jobs WHERE account=? AND room=? AND receipt_id=?',
                (job['account'],job['room'],receipt['message_id'])).fetchone()
            if duplicate: continue
            self.store.transition(job['id'],'uncertain','confirmed',receipt_id=receipt['message_id'],error_code=None)
            self.store.commit_facts(job)

    def resume_known_unsent(self,job):
        if not hasattr(self.sender,'prepared_matches') or not hasattr(self.sender,'resume_prepared'):return False
        if self.store.expired(job) or not job.get('answer_hash'):return False
        if self.store.memory_setting(job['room'],job['sender'])[1]!=job['context_epoch']:return False
        audit=self.store.db.execute('SELECT created,metadata FROM model_audits WHERE job_id=?',(job['id'],)).fetchone()
        if audit and json.loads(audit['metadata']).get('category')=='verified' and time.time()-audit['created']>120:return False
        # Never revive an old uncertainty or loop on an occupied editor.
        if self.store.db.execute("SELECT 1 FROM job_events WHERE job_id=? AND event IN ('uncertain','prepared_resume_attempt') LIMIT 1",(job['id'],)).fetchone():return False
        failure=self.store.db.execute("SELECT id FROM job_events WHERE job_id=? AND event='delivery_pre_send_error' ORDER BY id DESC LIMIT 1",(job['id'],)).fetchone()
        point=self.store.db.execute("SELECT id,metadata FROM job_events WHERE job_id=? AND event='delivery_checkpoint' ORDER BY id DESC LIMIT 1",(job['id'],)).fetchone()
        if not failure or not point or point['id']>failure['id']:return False
        checkpoint=json.loads(point['metadata'])
        try:
            if not self.sender.prepared_matches(job,checkpoint):return False
        except Exception:return False
        self.store.event(job['id'],'prepared_resume_attempt',phase=checkpoint.get('phase'))
        self.store.transition(job['id'],'attention','sending',send_started=time.time(),error_code=None)
        if hasattr(self.sender,'checkpoint_sink'):
            self.sender.checkpoint_sink=lambda **data:self.store.event(job['id'],'delivery_checkpoint',**data)
        try:
            receipt=self.sender.resume_prepared(job,checkpoint)
            if (not receipt or receipt.get('account')!=job['account'] or receipt.get('group')!=job['room']
                or receipt.get('reference_id')!=job['message_id'] or not receipt.get('message_id')
                or receipt.get('text_sha256')!=job['answer_hash']):raise RuntimeError('Unconfirmed resumed send')
            self.store.transition(job['id'],'sending','confirmed',receipt_id=receipt['message_id'],error_code=None)
            self.store.commit_facts(job)
        except DeliveryNotAttempted as exc:
            self.store.event(job['id'],'prepared_resume_stopped',reason=exc.code,known_unsent=True)
            self.store.transition(job['id'],'sending','attention',error_code='draft_or_target_changed')
        except Exception as exc:
            self.store.event(job['id'],'prepared_resume_stopped',error_type=type(exc).__name__,known_unsent=False)
            self.store.transition(job['id'],'sending','uncertain',error_code='send_unconfirmed')
        return True
