"""Called at 09:00/21:00 Beijing time by the authorized desktop automation."""
import json
from pathlib import Path
import time
from datetime import datetime,timedelta
from group_summary import TZ
from prepare_reply import export_inbox
from core import Store,Message

ROOT=Path(__file__).resolve().parent

def window_end(now):
    local=datetime.fromtimestamp(now,TZ)
    slots=[(local-timedelta(days=d)).replace(hour=h,minute=0,second=0,microsecond=0) for d in (0,1) for h in (9,21)]
    end=max(t.timestamp() for t in slots if t.timestamp()<=now)
    if now-end>3600:raise ValueError('outside_scheduled_window')
    return int(end)

def main():
    settings=json.loads((ROOT/'runtime/group-summary.json').read_text(encoding='utf-8-sig'))
    if not settings.get('enabled'):raise RuntimeError('summary_disabled')
    if (ROOT/'runtime/STOP').exists():raise RuntimeError('assistant_paused')
    now=time.time();end=window_end(now)
    cfg=export_inbox()['config']
    active=json.loads((ROOT/'runtime/activation.json').read_text())['activated_at']
    store=Store(ROOT/'runtime/state.sqlite3',cfg['account'],{cfg['group']},active)
    try:
        ident='summary:'+str(end)
        question='请总结本群此前12小时的聊天，简要列出讨论重点、已明确的结论和待办；不要公开个人或项目敏感信息。'
        accepted=store.ingest(Message(cfg['account'],cfg['group'],ident,'__scheduled_group_summary__',
            (cfg['account'],),question,now),now=now)
        row=store.db.execute('SELECT id,state FROM jobs WHERE account=? AND room=? AND message_id=?',
            (cfg['account'],cfg['group'],ident)).fetchone()
        print(json.dumps({'accepted':accepted,'job':dict(row) if row else None,'window_end':end}))
    finally:store.close()

if __name__=='__main__':main()
