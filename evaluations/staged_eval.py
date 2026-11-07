"""Live model / real staged scheduler / synthetic transport; never calls WeChat."""
import json
from pathlib import Path
import threading
import time
from core import Store,Message,Worker
from generation_pool import GenerationPool,GENERATION_WORKERS
from group20_eval import CASES,FakeSender
from investment_eval import EvaluationModel
from eval_assertions import assess
from evaluation_run import create_run

def main():
    root=create_run('staged-eval',CASES)
    print('RESULT_DIR='+str(root),flush=True)
    start=time.time();path=root/'synthetic.sqlite3'
    def factory():return Store(path,'synthetic-bot',{'synthetic-room'},start-1,max_queue_age=1800)
    store=factory();lock=threading.Lock();results={};counts={'active':0,'max':0}
    for i,(ident,q) in enumerate(CASES):
        assert store.ingest(Message('synthetic-bot','synthetic-room',ident,f'member-{i}',('synthetic-bot',),q,start))
    class Model(EvaluationModel):
        def __init__(self):super().__init__(timeout=150)
        def answer(self,q,history):
            ident=self.current_job['message_id'];began=time.time()
            with lock:counts['active']+=1;counts['max']=max(counts['max'],counts['active'])
            result={'id':ident,'status':'error','real_group_send':False,'queue_wait_seconds':round(began-start,2)}
            try:
                answer=super().answer(q,history)
                result.update(status='answered',answer=answer,audit=self.last_audit)
                return answer
            except Exception as exc:
                result['error_type']=type(exc).__name__;raise
            finally:
                result['seconds']=round(time.time()-began,2);result['checks']=assess(ident,result)
                with lock:counts['active']-=1;results[ident]=result
                payload=json.dumps(result,ensure_ascii=False,indent=2)
                (root/(ident+'-attempt-'+str(self.current_job['attempts'])+'.json')).write_text(payload,encoding='utf-8')
                (root/(ident+'.json')).write_text(payload,encoding='utf-8')
                print(json.dumps({k:result[k] for k in ('id','status','seconds','checks')},ensure_ascii=True),flush=True)
    class Sender(FakeSender):
        def send_and_verify(self,*args):
            result=super().send_and_verify(*args)
            self.sent[-1]['delivered_seconds']=round(time.time()-start,2)
            return result
    sender=Sender();delivery=Worker(store,None,sender,'send',stage='deliver')
    pool=GenerationPool(factory,Model,FakeSender,poll=.05);pool.start()
    try:
        deadline=time.monotonic()+1200
        while time.monotonic()<deadline:
            if pool.healthy_workers()!=GENERATION_WORKERS:raise RuntimeError('generation_worker_died')
            worked=delivery.run_one()
            if store.status()=={'confirmed':20}:break
            if not worked:time.sleep(.05)
        summary={'queue':store.status(),'max_parallel_models':counts['max'],'real_group_sends':0,
                 'simulated_sends':len(sender.sent),'elapsed_seconds':round(time.time()-start,2),
                 'model_seconds_sum':round(sum(r['seconds'] for r in results.values()),2),
                 'automated_failures':[k for k,r in results.items() if not r['checks']['passed']],
                 'receipt_order':[{'id':s['source'],'seconds':s['delivered_seconds']} for s in sender.sent]}
        (root/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(summary,ensure_ascii=True),flush=True)
    finally:pool.close();store.close()

if __name__=='__main__':main()
