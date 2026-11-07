"""Native image outbox. A persisted submission is inspected, never blindly resent."""
import base64
import hashlib
import json
import re
import time
import urllib.request
from protocol_transport import digest,TransportError

def manifest(job):
    images=json.loads(job.get('attachments') or '[]')
    if not isinstance(images,list) or len(images)>4:raise ValueError('invalid_images')
    for a in images:
        if (not re.fullmatch('[0-9a-f]{64}',a.get('sha256','')) or type(a.get('bytes')) is not int
            or not 0<a['bytes']<=6*1024*1024 or a.get('mime') not in ('image/jpeg','image/png','image/webp')):
            raise ValueError('invalid_image')
    if sum(a['bytes'] for a in images)>12*1024*1024:raise ValueError('invalid_images')
    return images

def validate_payload(job,images):
    expected=manifest(job)
    if len(expected)!=len(images):raise ValueError('image_manifest_mismatch')
    decoded=[]
    for a,b in zip(expected,images):
        raw=base64.b64decode(b.get('data',''),validate=True)
        if (a['sha256']!=b.get('sha256') or len(raw)!=a['bytes'] or hashlib.sha256(raw).hexdigest()!=a['sha256']):raise ValueError('image_changed')
        decoded.append(raw)
    return decoded

def setup(t):
    t.db.execute('CREATE TABLE IF NOT EXISTS image_intents (key TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,source_id TEXT)')
    t.db.commit()

def key_for(t,job,index):return 'image-'+digest([t.server_id,t.account,t.room,str(job['id']),index])
def fingerprint(a):return digest([a['sha256'],a['bytes'],a['mime']])

def upload(t,a,raw):
    boundary='image'+a['sha256'];ext={'image/jpeg':'jpg','image/png':'png','image/webp':'webp'}[a['mime']]
    header=f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="photo.{ext}"\r\nContent-Type: {a["mime"]}\r\n\r\n'.encode()
    body=header+raw+f'\r\n--{boundary}--\r\n'.encode()
    req=urllib.request.Request(t.endpoint+t.base+'/uploads',data=body,headers={
        'Authorization':'Bearer '+t.token,'Content-Type':'multipart/form-data; boundary='+boundary})
    try:
        with t.http.open(req,timeout=12) as r:source=json.load(r)
    except Exception:raise TransportError('image_upload_unavailable') from None
    if (source.get('sha256')!=a['sha256']
        or source.get('byteLength')!=a['bytes'] or not source.get('id')):raise ValueError('image_upload_mismatch')
    return source['id']

def read_receipt(t,key,a,source_id):
    intent=t.request(t.base+'/sends/by-idempotency-key?key='+key)
    if (intent.get('accountId'),intent.get('threadId'),intent.get('kind'),intent.get('sourceId'))!=(t.account,t.room,'send',source_id):
        raise ValueError('image_receipt_mismatch')
    result=intent.get('result') or {};ids=result.get('messageIds') or []
    if (intent.get('state') not in ('accepted','completed','succeeded') or len(ids)!=1
        or result.get('profileProjection') in ('unknown','not_applicable') or not intent.get('serverMessageId')):return None
    m=t.message(ids[0])
    if (m.get('accountId'),m.get('threadId'),m.get('direction'),m.get('type'),m.get('serverId'))!=(t.account,t.room,'outgoing',3,intent['serverMessageId']):
        raise ValueError('image_receipt_mismatch')
    return {'sha256':a['sha256'],'message_id':'s:'+intent['serverMessageId']}

def recover(t,job):
    setup(t);receipts=[]
    for i,a in enumerate(manifest(job)):
        key=key_for(t,job,i);row=t.db.execute('SELECT fingerprint,source_id FROM image_intents WHERE key=?',(key,)).fetchone()
        if not row or not row[1]:return None
        if row[0]!=fingerprint(a):raise ValueError('image_job_changed')
        try:r=read_receipt(t,key,a,row[1])
        except TransportError:return None
        if not r:return None
        receipts.append(r)
    return receipts

def deliver(t,job,decoded,check_source):
    setup(t);end=time.monotonic()+65;receipts=[]
    for i,(a,raw) in enumerate(zip(manifest(job),decoded)):
        key=key_for(t,job,i);fp=fingerprint(a)
        t.db.execute('BEGIN IMMEDIATE')
        old=t.db.execute('SELECT fingerprint,source_id FROM image_intents WHERE key=?',(key,)).fetchone()
        if old:
            t.db.rollback()
            if old[0]!=fp:raise ValueError('image_job_changed')
            source_id=old[1]
            if not source_id:raise RuntimeError('native_delivery_uncertain')
        else:
            # Persist before any external upload or message submission.
            t.db.execute('INSERT INTO image_intents VALUES(?,?,NULL)',(key,fp));t.db.commit()
            source_id=upload(t,a,raw)
            t.db.execute('UPDATE image_intents SET source_id=? WHERE key=?',(source_id,key));t.db.commit()
            check_source()
            try:t.request(t.base+'/sends',{'kind':'image','threadId':t.room,'sourceId':source_id,
                'standardSourceId':None,'thumbnailSourceId':None,'playSeconds':None,'durationMs':None},key)
            except TransportError:pass
        while True:
            try:r=read_receipt(t,key,a,source_id)
            except TransportError:r=None
            if r:receipts.append(r);break
            if time.monotonic()>end:raise RuntimeError('native_delivery_uncertain')
            time.sleep(.3)
    return receipts
