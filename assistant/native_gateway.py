"""Private local bridge to the authenticated native hub; exact original scope."""
import base64
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import urllib.parse
from protocol_transport import ProtocolTransport, TransportError, digest
import native_images

BASE=Path('/opt/wechat-agent-v2')

def settings():
    cfg=json.loads((BASE/'runtime/transport.json').read_text())
    original=json.loads((BASE/'runtime/account.json').read_text())
    if cfg.get('mode')!='native':raise RuntimeError('native_not_active')
    return cfg,original

def client():
    cfg,original=settings()
    return ProtocolTransport(cfg['endpoint'],Path(cfg['token_file']).read_text().strip(),
        cfg['server_id'],cfg['native_account'],original['group'],BASE/'runtime/native-delivery.sqlite')

def verify():
    cfg,original=settings();t=client()
    try:
        status=t.request('/v1/status');account=t.request(t.base)
        group=t.request(t.base+'/groups/'+urllib.parse.quote(original['group'],safe=''))
        return (status.get('serverId')==cfg['server_id'] and account.get('ownUsername')==original['account']
            and account.get('authenticated') is True and account.get('connection')=='online'
            and group.get('id')==original['group'] and group.get('isInGroup') is True)
    finally:t.close()

def source(job):
    from db_reader import Reader
    cfg,original=settings()
    if (job['account'],job['room'])!=(original['account'],original['group']):raise RuntimeError('scope_mismatch')
    ident=job['message_id']
    if not re.fullmatch(r's:[1-9]\d*',ident):raise RuntimeError('invalid_source_id')
    reader=Reader();table='Msg_'+hashlib.md5(original['group'].encode()).hexdigest();found=[]
    for path in sorted((reader.root/'message').glob('message_*.db')):
        if not re.fullmatch(r'message_\d+\.db',path.name):continue
        relative='message/'+path.name;db=reader.snapshot(relative)
        try:
            if not db.execute('SELECT 1 FROM sqlite_master WHERE name=?',(table,)).fetchone():continue
            for row in db.execute(f'SELECT local_id,local_type FROM {table} WHERE server_id=?',(int(ident[2:]),)):
                raw=json.dumps({'database':relative,'table':table,'localId':str(row['local_id'])},separators=(',',':')).encode()
                found.append(('msg_'+base64.urlsafe_b64encode(raw).decode().rstrip('='),row['local_type']&0xffffffff))
        finally:db.close()
    if len(found)!=1:raise RuntimeError('source_not_unique')
    t=client()
    try:
        m=t.message(found[0][0])
        if m.get('recall'):return {'source_state':'revoked'}
        if (m.get('accountId'),m.get('threadId'),m.get('serverId'),m.get('senderId'))!=(cfg['native_account'],original['group'],ident[2:],job['sender']):raise RuntimeError('source_mismatch')
        return {'source_state':'available','message':m}
    finally:t.close()

def plain_send(t,job,text):
    # Only trusted local privacy warnings and scheduled summaries use this path.
    key='plain-'+digest([t.server_id,t.account,t.room,str(job['id'])]);fp=digest([key,text])
    t.db.execute('BEGIN IMMEDIATE')
    old=t.db.execute('SELECT fingerprint FROM intents WHERE job=?',(key,)).fetchone()
    if old:
        t.db.rollback()
        if old[0]!=fp:raise RuntimeError('job_changed')
    else:
        t.db.execute('INSERT INTO intents VALUES(?,?,?)',(key,fp,'submission_started'));t.db.commit()
        try:t.request(t.base+'/sends',{'kind':'text','threadId':t.room,'text':text},key)
        except TransportError:pass
    end=time.monotonic()+20
    while True:
        intent=t.request(t.base+'/sends/by-idempotency-key?key='+key)
        if intent.get('state')=='accepted' and intent.get('localMessageId'):
            m=t.message(intent['localMessageId'])
            if (intent.get('accountId'),intent.get('destination'),m.get('accountId'),m.get('threadId'),m.get('body'),m.get('direction'),m.get('serverId'))!=(t.account,t.room,t.account,t.room,text,'outgoing',intent.get('serverId')) or not intent.get('serverId'):raise RuntimeError('receipt_mismatch')
            t.db.execute('UPDATE intents SET state=? WHERE job=?',('hub_verified',key));t.db.commit()
            return {'state':'hub_verified','server_id':intent['serverId']}
        if intent.get('state') in ('failed','cancelled','needs_review') or time.monotonic()>end:return {'state':'needs_review'}
        time.sleep(.2)

def recover_receipt(t,job):
    """Read only: an uncertain job can only match its durable native intent."""
    matches=[]
    for prefix in ('candidate-','plain-'):
        key=prefix+digest([t.server_id,t.account,t.room,str(job['id'])])
        if t.db.execute('SELECT 1 FROM intents WHERE job=?',(key,)).fetchone():matches.append((key,prefix))
    if not matches:return None
    if len(matches)!=1:raise RuntimeError('ambiguous_intent')
    key,prefix=matches[0]
    try:intent=t.request(t.base+'/sends/by-idempotency-key?key='+key)
    except TransportError as exc:
        if str(exc) in ('http_404','transport_unavailable'):return None
        raise
    if intent.get('state')!='accepted' or not intent.get('localMessageId'):return None
    m=t.message(intent['localMessageId'])
    if (intent.get('accountId'),intent.get('destination'),m.get('id'),m.get('accountId'),
        m.get('threadId'),m.get('direction'),m.get('serverId'))!=(t.account,t.room,
        intent['localMessageId'],t.account,t.room,'outgoing',intent.get('serverId')) or not intent.get('serverId'):
        raise RuntimeError('receipt_mismatch')
    body=m.get('body')
    if not isinstance(body,str) or hashlib.sha256(body.encode()).hexdigest()!=job['answer_hash']:
        raise RuntimeError('receipt_mismatch')
    quoted=prefix=='candidate-'
    if quoted and (m.get('type'),m.get('appMessageType'),(m.get('quote') or {}).get('serverId'))!=(49,57,job['message_id'][2:]):
        raise RuntimeError('receipt_mismatch')
    t.db.execute('UPDATE intents SET state=? WHERE job=?',('hub_verified',key));t.db.commit()
    images=native_images.recover(t,job)
    if images is None:return None
    return {'account':job['account'],'group':job['room'],'message_id':'s:'+intent['serverId'],
        'images':images,
        'reference_id':job['message_id'] if quoted else None,'text_sha256':job['answer_hash'],
        'evidence':'native_server_acceptance_and_profile_readback'}


def dispatch(req):
    cfg,original=settings();op=req['op']
    if op=='verify':return {'verified':verify()}
    job=req.get('job')
    if op=='source':
        result=source(job);return {'source_state':result['source_state']}
    if op=='receipt':
        if (job['account'],job['room'])!=(original['account'],original['group']):raise RuntimeError('scope_mismatch')
        if not verify():return None
        t=client()
        try:return recover_receipt(t,job)
        finally:t.close()
    if op!='send':raise RuntimeError('unsupported_operation')
    if not verify():raise RuntimeError('native_offline')
    if (job['account'],job['room'])!=(original['account'],original['group']):raise RuntimeError('scope_mismatch')
    text=req['text']
    if not isinstance(text,str) or not text.strip() or len(text)>4000:raise RuntimeError('invalid_answer')
    decoded=native_images.validate_payload(job,req.get('images',[]))
    summary=(job.get('sender')=='__scheduled_group_summary__' and re.fullmatch(r'summary:\d+',job.get('message_id','')))
    quoted=not (summary or job.get('privacy_warning') is True)
    t=client()
    try:
        if quoted:
            checked=source(job)
            if checked['source_state']=='revoked':raise RuntimeError('source_revoked')
            m=checked['message']
            result=t.deliver({'id':str(job['id']),'account':cfg['native_account'],'room':job['room'],
                'source_id':m['id'],'source_server_id':m['serverId'],'sender':job['sender'],'answer':text},timeout=20)
        else:result=plain_send(t,job,text)
        if result['state']=='failed' and result.get('error_code') in ('transport_before_submission','service_rejected','send_admission_revoked','quote_target_unavailable'):
            raise RuntimeError('native_'+result['error_code'])
        if result['state']!='hub_verified':raise RuntimeError('native_delivery_uncertain')
        def check_source():
            if not verify():raise RuntimeError('native_offline')
            if quoted and source(job)['source_state']=='revoked':raise RuntimeError('native_delivery_uncertain')
        images=native_images.deliver(t,job,decoded,check_source) if decoded else []
        return {'account':job['account'],'group':job['room'],'message_id':'s:'+result['server_id'],
            'images':images,
            'reference_id':job['message_id'] if quoted else None,'text_sha256':hashlib.sha256(text.encode()).hexdigest(),
            'evidence':'native_server_acceptance_and_profile_readback'}
    finally:t.close()

if __name__=='__main__':
    try:print(json.dumps(dispatch(json.load(sys.stdin))))
    except Exception as exc:
        # Arbitrary exception content may contain paths, identifiers or payloads.
        code=str(exc) if str(exc) in ('source_revoked','native_offline','source_not_unique','scope_mismatch','native_delivery_uncertain','native_transport_before_submission','native_service_rejected','native_send_admission_revoked','native_quote_target_unavailable') else 'native_gateway_failed'
        print(json.dumps({'error':code}));sys.exit(1)
