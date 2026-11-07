"""Two operator-authorized public-data dry-runs; never sends WeChat messages."""
import concurrent.futures,json,time
from codex_client import Codex,ROOT
from futu_evidence import TOOLS


class Observed(Codex):
    def __init__(self):super().__init__(timeout=150);self.calls=[]
    def receive(self):
        event=super().receive()
        if event.get('method')=='item/completed':
            item=event.get('params',{}).get('item',{})
            if item.get('type')=='mcpToolCall':
                self.calls.append({k:item.get(k) for k in ('server','tool','status','readOnlyHint')})
        return event


def run(case):
    name,question=case;m=Observed();start=time.monotonic()
    try:r={'answer':m.answer(question),'audit':m.last_audit,'status':'answered'}
    except Exception as e:r={'status':'error','error_type':type(e).__name__}
    r.update({'id':name,'elapsed_seconds':round(time.monotonic()-start,2),'mcp_calls':m.calls,
              'only_allowed_tools':all(c['server']=='futu_research' and c['tool'] in TOOLS for c in m.calls),'sent_to_group':False})
    (ROOT/'runtime/futu'/(name+'-answer.json')).write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8')
    return r


if __name__=='__main__':
    cases=[('quote','腾讯和美团最新股价、涨跌和行情时间分别是多少？优先查富途，休市不要当今日成交。'),
           ('financial','美团最新已披露单季度的收入和归母净利润是多少？优先查富途财报，明确报告期、币种和单位，不要把半年累计当一个季度。')]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        for result in pool.map(run,cases):print(json.dumps(result,ensure_ascii=True),flush=True)
