"""Public valuation latency check, production model, no group sends/history."""
import json,time
from investment_eval import EvaluationModel
from evaluation_run import create_run

if __name__=='__main__':
    question='分析一下贵州茅台的估值。'
    target=create_run('valuation-latency',[('maotai',question,[])])
    m=EvaluationModel(timeout=150);start=time.monotonic()
    try:r={'answer':m.answer(question),'audit':m.last_audit,'status':'answered'}
    except Exception as exc:r={'status':'error','error_type':type(exc).__name__}
    r.update(seconds=round(time.monotonic()-start,2),web=m.web_observations,sources=m.draft_sources,real_send=False)
    (target/'result.json').write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'directory':str(target),**r},ensure_ascii=False))
