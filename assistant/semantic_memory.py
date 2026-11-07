"""Offline Chinese semantic recall; vectors remain in the scoped local store."""
from pathlib import Path
import hashlib
import threading
import numpy as np

_model=None
_lock=threading.Lock()
MODEL='BAAI/bge-small-zh-v1.5'

def vectors(texts):
    global _model
    with _lock:
        if _model is None:
            from fastembed import TextEmbedding
            _model=TextEmbedding(MODEL,cache_dir=str(Path(__file__).parent/'runtime/models/embedding'),local_files_only=True)
        return [np.asarray(v,dtype=np.float32) for v in _model.embed(texts)]

class SemanticRecall:
    def __init__(self,store): self.store=store
    def __call__(self,job):
        db=self.store.db
        args=(job['account'],job['room'],job['sender'],job['id'])
        # Scope and privacy are checked before any record is embedded.
        if job['account']!=self.store.account or job['room'] not in self.store.groups: raise ValueError('scope_mismatch')
        if not self.store.memory_setting(job['room'],job['sender'])[0]: return []
        rows=db.execute("SELECT * FROM jobs WHERE account=? AND room=? AND sender=? AND id<? AND state='confirmed' AND question!=''",args).fetchall()
        if not rows: return []
        indexed={r[0]:r[1] for r in db.execute('SELECT DISTINCT job_id,content_hash FROM semantic_chunks WHERE model=? AND job_id IN (SELECT id FROM jobs WHERE account=? AND room=? AND sender=? AND id<?)',(MODEL,*args))}
        for row in rows:
            text=row['question']+'\n'+self.store.context_answer(row)
            digest=hashlib.sha256(text.encode()).hexdigest()
            if indexed.get(row['id'])==digest: continue
            chunks=[text[i:i+900] for i in range(0,len(text),750)]
            embeddings=vectors(chunks)
            db.execute('BEGIN IMMEDIATE')
            try:
                # A concurrent forget must not leave a resurrected vector.
                live=db.execute("SELECT * FROM jobs WHERE id=?",(row['id'],)).fetchone()
                if (live and live['question'] and self.store.memory_setting(job['room'],job['sender'])[0]
                    and hashlib.sha256((live['question']+'\n'+self.store.context_answer(live)).encode()).hexdigest()==digest):
                    db.execute('DELETE FROM semantic_chunks WHERE job_id=?',(row['id'],))
                    db.executemany('INSERT INTO semantic_chunks VALUES(?,?,?,?,?)',[(row['id'],i,digest,MODEL,v.tobytes()) for i,v in enumerate(embeddings)])
                db.execute('COMMIT')
            except BaseException: db.execute('ROLLBACK'); raise
        query=vectors([job['question']])[0]; query=query/(np.linalg.norm(query)+1e-9)
        scores={}
        for r in db.execute('''SELECT v.job_id,v.embedding FROM semantic_chunks v JOIN jobs j ON j.id=v.job_id
            WHERE j.account=? AND j.room=? AND j.sender=? AND j.id<? AND j.question!='' AND j.state='confirmed' AND v.model=?''',(*args,MODEL)):
            value=np.frombuffer(r['embedding'],dtype=np.float32)
            score=float(np.dot(query,value)/(np.linalg.norm(value)+1e-9))
            scores[r['job_id']]=max(scores.get(r['job_id'],-1),score)
        # Recall scores rank candidate context; they are not factual confidence.
        ids=[i for i,score in sorted(scores.items(),key=lambda x:x[1],reverse=True)[:5] if score>=.4]
        return [dict(r) for r in rows if r['id'] in ids]
