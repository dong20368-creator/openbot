"""Local status metadata only; never includes message text, URLs or identities."""
import json
import time

def snapshot(store,now=None):
    now=time.time() if now is None else now
    scope,args=store.scope()
    active=[dict(r) for r in store.db.execute(f'''SELECT id,state,attempts,error_code FROM jobs
        WHERE {scope} AND state IN ('generating','dispatching','sending','ready','retry') ORDER BY id''',args)]
    oldest=store.db.execute(f"SELECT min(created) FROM jobs WHERE {scope} AND state IN ('pending','retry','ready')",args).fetchone()[0]
    recent=store.db.execute(f'''SELECT a.job_id,a.created,a.metadata FROM model_audits a JOIN jobs j
        ON j.id=a.job_id WHERE {scope} ORDER BY a.created DESC,a.job_id DESC LIMIT 1''',args).fetchone()
    return {'jobs':store.status(),'current':active[0] if active else None,'active_jobs':active,
            'oldest_wait_seconds':round(max(0,now-oldest),2) if oldest is not None else 0,
            'last_model_job_id':recent['job_id'] if recent else None,
            'last_model_completed_at':recent['created'] if recent else None,
            'last_model_age_seconds':round(max(0,now-recent['created']),2) if recent else None,
            'last_model_audit':json.loads(recent['metadata']) if recent else None}

def monitor_loop(stop,publish,error,interval=2):
    """Transient read/write errors must not silently kill the heartbeat."""
    while not stop.is_set():
        try:publish()
        except Exception:
            try:error()
            except Exception:pass
        stop.wait(interval)
