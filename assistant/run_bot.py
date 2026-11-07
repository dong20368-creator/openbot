"""Single-instance local worker. Stop with runtime/STOP; no network listener."""
import argparse
import json
import msvcrt
import os
from pathlib import Path
import time
import threading
from collector import Collector
from core import Message,Store,Worker
from codex_client import Codex
from native_sender import NativeSender
from prepare_reply import export_inbox
from media_context import MediaContext
from semantic_memory import SemanticRecall
from generation_pool import GenerationPool,GENERATION_WORKERS
from runtime_health import snapshot as health_snapshot,monitor_loop
from backend_preflight import verify as verify_backend_dependencies

ROOT=Path(__file__).resolve().parent


def status(**values):
    path=ROOT/'runtime/bot-status.json'
    tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps({'pid':os.getpid(),'updated':time.time(),**values}),encoding='utf-8')
    tmp.replace(path)


def main(check=False):
    with open(ROOT/'runtime/worker.lock','a+b') as lock:
        lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
        msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        try:
            while True:
                try:
                    dependencies=verify_backend_dependencies()
                    data=export_inbox(); config=data['config']; break
                except Exception as exc:
                    if check: raise
                    status(state='starting',reason='backend_dependencies_unavailable'
                        if str(exc)=='backend_dependencies_unavailable' else 'waiting_for_wechat')
                    if (ROOT/'runtime/STOP').exists(): return
                    time.sleep(5)
            sender=NativeSender(config)
            if check:
                target=sender.verify_target(config['account'],config['group'])
                if not target:raise RuntimeError('target_verification_failed')
                print(json.dumps({'target_verified':target,'inbox_mentions':len(data['messages']),
                    'backend_dependencies_verified':dependencies['ok']})); return
            active=json.loads((ROOT/'runtime/activation.json').read_text())['activated_at']
            store=Store(ROOT/'runtime/state.sqlite3',config['account'],{config['group']},active,max_queue_age=1800)
            store.recover()
            store.semantic=SemanticRecall(store)
            for msg in data['messages']: store.ingest(Message(**msg))
            collector=Collector(ROOT/'runtime/state.sqlite3',config,active); collector.start()
            def generation_store():
                result=Store(ROOT/'runtime/state.sqlite3',config['account'],{config['group']},active,max_queue_age=1800)
                result.semantic=SemanticRecall(result)
                return result
            def generation_model():
                result=Codex(timeout=150,reuse_process=True);result.media_provider=MediaContext()
                return result
            def intake_healthy():
                value,_=collector.snapshot()
                return value['healthy'] and time.time()-value['last_seen']<20
            generators=GenerationPool(generation_store,generation_model,lambda:NativeSender(config),
                healthy=intake_healthy,stopping=lambda:(ROOT/'runtime/STOP').exists(),workers=GENERATION_WORKERS,on_ready=collector.changed.set)
            collector.on_jobs=generators.wake
            worker=Worker(store,None,sender,mode='send',context_enabled=True,stage='deliver')
            monitor_stop=threading.Event()
            ui_health={'ready':True}
            def monitor():
                view=Store(ROOT/'runtime/state.sqlite3',config['account'],{config['group']},active)
                try:
                    def publish():
                        incoming,_=collector.snapshot()
                        detail=health_snapshot(view)
                        state='running' if incoming['healthy'] and time.time()-incoming['last_seen']<20 else 'degraded'
                        counts=detail['jobs']
                        if state=='running' and not ui_health['ready'] and any(counts.get(s) for s in ('pending','retry','ready')): state='paused'
                        if counts.get('attention') or counts.get('uncertain'): state='attention'
                        if generators.healthy_workers()!=GENERATION_WORKERS and not (ROOT/'runtime/STOP').exists():state='degraded'
                        if incoming.get('reason')=='native_auth_required':state='needs_login'
                        status(state=state,intake=incoming,**detail,
                            generation_workers=generators.healthy_workers(),generation_limit=GENERATION_WORKERS,
                            generating_count=counts.get('generating',0))
                    monitor_loop(monitor_stop,publish,lambda:status(state='degraded',reason='status_read_or_write_failed'))
                finally: view.close()
            generators.start()
            monitor_thread=threading.Thread(target=monitor,name='wechat-status',daemon=True); monitor_thread.start()
            reconciled=0
            try:
                while not (ROOT/'runtime/STOP').exists():
                    if generators.healthy_workers()!=GENERATION_WORKERS:
                        raise RuntimeError('generation_worker_stopped')
                    if not monitor_thread.is_alive():raise RuntimeError('status_monitor_stopped')
                    collector.changed.clear()
                    try:
                        health,latest_config=collector.snapshot(); sender.config=latest_config
                        if health['healthy'] and time.time()-health['last_seen']<20:
                            if time.monotonic()-reconciled>30:
                                reconciled=time.monotonic();worker.reconcile()
                            scope,args=store.scope()
                            due=store.db.execute(f"SELECT 1 FROM jobs WHERE {scope} AND state='ready' AND next_attempt<=? LIMIT 1",(*args,time.time())).fetchone()
                            if due and not store.delivery_blocked(sender.independent_intents):
                                # Native delivery has no GUI to focus. Worker still
                                # verifies the account/room immediately before send;
                                # gateway independently enforces its scope too.
                                if worker.run_one():
                                    generators.wake()
                                    continue
                    except Exception as exc:
                        store.event(None,'worker_error',error_type=type(exc).__name__)
                    collector.changed.wait(1)
            finally:
                generators.close();collector.close(); monitor_stop.set(); monitor_thread.join(timeout=5)
                status(state='stopped',jobs=store.status()); store.close()
        finally:
            lock.seek(0); msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--check',action='store_true')
    main(parser.parse_args().check)
