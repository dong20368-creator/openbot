"""Public image acceptance; prepares artifacts but never sends to WeChat."""
import json
import time
from investment_eval import EvaluationModel
from evaluation_run import create_run

class Model(EvaluationModel):
    def __init__(self):super().__init__(timeout=150);self.image_trace=[]
    def receive(self):
        e=super().receive()
        if e.get('method')=='item/tool/call' and e.get('params',{}).get('namespace')=='public_images':
            self.image_trace.append({k:e['params'].get(k) for k in ('tool','arguments')})
        return e

if __name__=='__main__':
    q='找一下Qwen官方账号最近在X发的那张图片，解释内容并把原图发到群里。'
    target=create_run('outbound-image',[('qwen',q,[])])
    m=Model();start=time.monotonic()
    try:r={'answer':m.answer(q),'audit':m.last_audit,'attachments':m.last_attachments,'status':'answered'}
    except Exception as exc:r={'status':'error','error_type':type(exc).__name__}
    r.update(seconds=round(time.monotonic()-start,2),image_trace=m.image_trace,web=m.web_observations,real_send=False)
    (target/'result.json').write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'directory':str(target),**r},ensure_ascii=False))
