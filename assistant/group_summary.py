"""Explicit scheduled group-summary scope, separate from member memories."""
import json
import os
from pathlib import Path
import re
import subprocess
from datetime import datetime,timezone,timedelta

ROOT=Path(__file__).resolve().parent
TZ=timezone(timedelta(hours=8))

def is_summary(job):return bool(re.fullmatch(r'summary:\d+',job.get('message_id','')))

def compact_summary(text):
    return '\n'.join(re.sub(r'[^\S\n]+',' ',line).strip() for line in text.splitlines() if line.strip())

def scrub(text):
    # Drop high-risk lines locally; the model must also omit personal details.
    kept=[]
    for line in text.splitlines():
        if re.search(r'密码|验证码|密钥|私钥|身份证|银行卡|住址|家庭地址|恢复码|token|api[_ -]?key',line,re.I):continue
        line=re.sub(r'https?://\S+','[链接]',line)
        line=re.sub(r'(?<!\d)1[3-9]\d{9}(?!\d)|(?<!\d)\d{15,19}(?!\d)|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}','[联系方式已省略]',line)
        line=re.sub(r'@[\w\u4e00-\u9fff]+','[成员]',line)
        if line.strip():kept.append(line)
    return '\n'.join(kept)

def build_material(data,max_chars=60000):
    rows=[];used=0;omitted=0;redacted=0
    for msg in data['messages']:
        clean=scrub(msg['text'])
        if not clean:redacted+=1;continue
        row={'member':msg['speaker'],'time':datetime.fromtimestamp(msg['created'],TZ).strftime('%H:%M'),'text':clean}
        cost=len(json.dumps(row,ensure_ascii=False))
        if used+cost>max_chars:omitted+=1;continue
        rows.append(row);used+=cost
    return dict(kind='authorized_group_summary',start=datetime.fromtimestamp(data['start'],TZ).isoformat(),
        end=datetime.fromtimestamp(data['end'],TZ).isoformat(),records=rows,total_text_messages=len(data['messages']),
        omitted_for_size=omitted,redacted_messages=redacted,excluded=data['excluded'],
        notice='仅本机已同步的本群文字及文字引用，不含图片/附件正文/语音/视频。不保证本机已同步全部历史。')

def load_material(job):
    consent=json.loads((ROOT/'runtime/group-summary.json').read_text(encoding='utf-8-sig'))
    if not consent.get('enabled') or not is_summary(job):raise ValueError('summary_not_enabled')
    request={'account':job['account'],'group':job['room'],'end':int(job['message_id'].split(':')[1])}
    run=subprocess.run(['wsl','-d','WeChatAgent','-u','root','--exec','python3','/opt/wechat-agent-v2/group_window.py'],
        input=json.dumps(request),capture_output=True,encoding='utf-8',timeout=45,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    data=json.loads(run.stdout)
    if run.returncode or data.get('error'):raise ValueError('group_window_unavailable')
    if data['end']!=request['end'] or data['start']!=request['end']-43200:raise ValueError('window_mismatch')
    return build_material(data)
