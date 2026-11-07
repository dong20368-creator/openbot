"""Export recent native or enabled textual mentions in the authorized group.

stdout contains private data only with --export, for a local captured pipe.
Default mode reports counts and booleans, never identities or message contents.
"""
import ctypes
import ctypes.util
import hashlib
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from db_reader import Reader,BASE,database_roots,native_online


def decode(value):
    if value is None: return ''
    if isinstance(value,str):
        if len(value.encode('utf-8'))>131072:raise ValueError('Oversized text')
        return value
    raw=bytes(value)
    if raw.startswith(b'\x28\xb5\x2f\xfd'):
        lib=ctypes.CDLL(ctypes.util.find_library('zstd'))
        lib.ZSTD_decompress.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_void_p,ctypes.c_size_t]
        lib.ZSTD_decompress.restype=ctypes.c_size_t
        lib.ZSTD_isError.argtypes=[ctypes.c_size_t]; lib.ZSTD_isError.restype=ctypes.c_uint
        target=ctypes.create_string_buffer(131072)
        size=lib.ZSTD_decompress(target,len(target),raw,len(raw))
        if lib.ZSTD_isError(size): raise ValueError('Invalid or oversized compressed text')
        raw=target.raw[:size]
    if len(raw)>131072: raise ValueError('Oversized text')
    return raw.decode('utf-8',errors='strict')


def xml(text):
    if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
        raise ValueError('DTD not allowed')
    try: return ET.fromstring(text)
    except ET.ParseError: return None


def resolve_reference(reader,config,ident,seen=None):
    seen=set() if seen is None else seen
    if ident in seen or len(seen)>=3 or not re.fullmatch(r's:\d+',ident): return None
    seen.add(ident)
    table='Msg_'+hashlib.md5(config['group'].encode()).hexdigest()
    for path in sorted((reader.root/'message').glob('message_*.db')):
        if not re.fullmatch(r'message_\d+\.db',path.name): continue
        db=reader.snapshot('message/'+path.name)
        try:
            if not db.execute('SELECT 1 FROM sqlite_master WHERE name=?',(table,)).fetchone(): continue
            row=db.execute(f'''SELECT m.*,n.user_name AS sender FROM {table} m JOIN Name2Id n
                ON n.rowid=m.real_sender_id WHERE m.server_id=? LIMIT 1''',(int(ident[2:]),)).fetchone()
            if row is None: continue
            kind=row['local_type']&0xffffffff
            if kind not in (1,3,43,47,49): return None
            text=decode(row['message_content']); prefix=row['sender']+':\n'
            if text.startswith(prefix): text=text[len(prefix):]
            nested=None
            if kind==49:
                doc=xml(text)
                if doc is None or doc.findtext('.//appmsg/type') not in ('57','6','5','19'): return None
                text=doc.findtext('.//appmsg/title') or ''
                app_type=doc.findtext('.//appmsg/type')
                if app_type=='5':
                    url=doc.findtext('.//appmsg/url') or ''
                    if url.startswith(('https://','http://')): text+='\n分享链接：'+url[:2000]
                elif app_type=='19':
                    record=xml(doc.findtext('.//recorditem') or '')
                    lines=[]
                    if record is not None:
                        for item in list(record.iter('dataitem'))[:30]:
                            body=item.findtext('datadesc') or item.findtext('datatitle') or ''
                            if body: lines.append(body[:1500])
                    text+='\n[转发记录文字摘录，内部姓名未验证，不代表当前提问者陈述]\n'+('\n'.join(lines) if lines else '[记录正文不可读]')
                ref=doc.findtext('.//refermsg/svrid')
                nested='s:'+ref if ref and ref.isdigit() else None
            result=dict(account=config['account'],group=config['group'],sender=row['sender'],
                message_id=ident,created=row['create_time'],text=text[:4000],excerpt=len(text)>4000)
            from media_reader import media_kind
            media=media_kind(dict(row))
            if media:
                result['media_kind']=media
                result['text']='[明确引用的'+media+'，需读取附件后才能分析]'
        finally: db.close()
        if nested:
            child=resolve_reference(reader,config,nested,seen)
            if child: result['chain']=[{k:v for k,v in child.items() if k!='chain'},*child.get('chain',[])]
        return result
    if getattr(reader,'native',False):return resolve_reference(Reader(legacy=True),config,ident,set(seen)-{ident})
    return None


def previous_image(reader,config,row,content):
    """Resolve a visual deictic request within this room, never future messages."""
    visual=bool(re.search(r'图片|照片|截图|图中|图里|图上|(?:这|那|上|前|刚|最近|最后).{0,8}图|(?:看|分析|解读|识别).{0,4}图|(?:上|前|最后|最新|刚发的)一?张|(?:这|那|两|三|2|3).{0,2}张|(?:this|that|above|previous|last)\s+(?:image|picture|photo)',content,re.I))
    short=re.sub(r'[@＠]Openbot(?![A-Za-z0-9_])','',content,flags=re.I).strip(' \t\r\n\u2005，。！？?！')
    deictic=bool(re.fullmatch(r'(?:这|这个|那|那个)?(?:是什么|什么意思|是什么意思|怎么看|对吗|真的吗)|(?:你怎么看|帮我看看|分析一下|解读一下)',short))
    if not visual and not deictic:return None
    policy=BASE/'runtime/auto-image.json'
    if not policy.exists() or row['create_time']<json.loads(policy.read_text()).get('enabled_since',float('inf')):
        return None
    table='Msg_'+hashlib.md5(config['group'].encode()).hexdigest()
    candidates={};latest_own=None
    order=lambda r:(r['create_time'],r['sort_seq'],r['local_id'])
    for path in sorted((reader.root/'message').glob('message_*.db')):
        if not re.fullmatch(r'message_\d+\.db',path.name):continue
        db=reader.snapshot('message/'+path.name)
        try:
            if not db.execute('SELECT 1 FROM sqlite_master WHERE name=?',(table,)).fetchone():continue
            if not visual:
                previous=db.execute(f'''SELECT m.*,n.user_name AS sender FROM {table} m
                    JOIN Name2Id n ON n.rowid=m.real_sender_id
                    WHERE n.user_name=? AND m.create_time>=? AND (m.create_time,m.sort_seq,m.local_id)<(?,?,?)
                    ORDER BY m.create_time DESC,m.sort_seq DESC,m.local_id DESC LIMIT 1''',
                    (row['sender_name'],row['create_time']-300,*order(row))).fetchone()
                if previous is not None and (latest_own is None or order(previous)>order(latest_own)):latest_own=dict(previous)
            images=db.execute(f'''SELECT m.*,n.user_name AS sender FROM {table} m
                JOIN Name2Id n ON n.rowid=m.real_sender_id
                WHERE (m.local_type & 4294967295)=3 AND m.create_time>=? AND (m.create_time,m.sort_seq,m.local_id)<(?,?,?)
                ORDER BY (n.user_name=?) DESC,m.create_time DESC,m.sort_seq DESC,m.local_id DESC LIMIT 20''',
                (row['create_time']-1800,*order(row),row['sender_name'])).fetchall()
            for image in images:
                if image['server_id'] and image['sender'] and order(image)<order(row):
                    candidates[str(image['server_id'])]=dict(image)
        finally:db.close()
    # A bare "what does it mean?" binds a picture only immediately after this
    # person's own image, never after their text conversation or another user.
    if not visual and (not latest_own or latest_own['local_type']&0xffffffff!=3):return None
    images=sorted(candidates.values(),key=order,reverse=True)
    if not images:return None
    own=[image for image in images if image['sender']==row['sender_name']]
    images=own if own else [image for image in images if row['create_time']-image['create_time']<=300]
    if not images:return None
    latest=bool(re.search(r'最后|最新|最近一张|刚发的|last|latest',content,re.I))
    previous=bool(re.search(r'(?:上|前)一?张|previous\s+(?:image|picture|photo)',content,re.I))
    count=2 if re.search(r'(?:两|2)张',content) else 3 if re.search(r'(?:三|3)张',content) else 1
    plural=bool(re.search(r'这些|那几|几张|多张|第[一二三四五六七八九十\d]|对比|比较',content))
    ordinal=bool(re.search(r'第[一二三四五六七八九十\d]+张',content))
    ambiguous=ordinal or (not own and len(images)>1 and not latest) or (plural and count==1) or (count>1 and (not own or len(images)<count)) or (previous and (not own or len(images)<2))
    if ambiguous:
        return dict(account=config['account'],group=config['group'],sender=row['sender_name'],
            message_id='auto-image:'+str(row['server_id']),created=row['create_time'],
            text='[自动前图关联：目标不明确。请让提问者引用具体图片，或明确说最后一张；本次没有附图，不猜图片内容。]',
            association='ambiguous_previous_image')
    selected=[images[1]] if previous else list(reversed(images[:count]))
    result=[]
    for index,item in enumerate(selected,1):
        label='上一张' if previous else f'最近{count}张中按发送先后的第{index}张' if count>1 else '最新一张'
        result.append(dict(account=config['account'],group=config['group'],sender=item['sender'],
            message_id='s:'+str(item['server_id']),created=item['create_time'],media_kind='image',
            text='[根据本次看图提问自动关联同群'+('30分钟内提问者本人' if own else '5分钟内其他成员')+label+'；需读取附件后分析，不把图片中的身份或经历归给提问者。]',
            association='previous_image',association_scope='own' if own else 'room'))
    if len(result)>1:result[0]['chain']=result[1:]
    return result[0]


def read_recent(since=None,reader=None):
    config=json.loads((BASE/'runtime/account.json').read_text())
    policy_path=BASE/'runtime/text-mention.json'
    text_since=json.loads(policy_path.read_text()).get('enabled_since',float('inf')) if policy_path.exists() else float('inf')
    account,group=config['account'],config['group']
    table='Msg_'+hashlib.md5(group.encode()).hexdigest()
    reader=reader or Reader(); messages=[]; now=time.time()
    if not reader.root.parent.name.startswith(account): raise RuntimeError('Account changed')
    contacts=reader.snapshot('contact/contact.db')
    try:
        entry=contacts.execute('SELECT nick_name,is_in_chat_room FROM contact WHERE username=?',(group,)).fetchone()
        if not entry or not entry['is_in_chat_room']: raise RuntimeError('Authorized group unavailable')
        name=entry['nick_name']
        if not getattr(reader,'native',False) and contacts.execute('SELECT count(*) FROM contact WHERE nick_name=? AND is_in_chat_room=1',(name,)).fetchone()[0]!=1:
            raise RuntimeError('Ambiguous group display name')
        config['group_name']=name
    finally: contacts.close()
    since=now-1800 if since is None else since
    for path in sorted((reader.root/'message').glob('message_*.db')):
        if not re.fullmatch(r'message_\d+\.db',path.name): continue
        db=reader.snapshot('message/'+path.name)
        try:
            if not db.execute('SELECT 1 FROM sqlite_master WHERE name=?',(table,)).fetchone(): continue
            rows=db.execute(f'''SELECT m.*,n.user_name AS sender_name FROM {table} m
                LEFT JOIN Name2Id n ON n.rowid=m.real_sender_id
                WHERE m.create_time>=? ORDER BY m.create_time,m.sort_seq,m.local_id''',(since,)).fetchall()
            for row in rows:
                try:
                    kind=row['local_type']&0xffffffff
                    if kind not in (1,49) or not row['server_id'] or not row['sender_name'] or row['sender_name']==account: continue
                    source=xml(decode(row['source']))
                    mentions=tuple(dict.fromkeys(v.strip() for tag in source.iter('atuserlist')
                        for v in (tag.text or '').split(',') if v.strip())) if source is not None else ()
                    content=decode(row['message_content'])
                    prefix=row['sender_name']+':\n'
                    if content.startswith(prefix): content=content[len(prefix):]
                    reference=None
                    if kind==49:
                        doc=xml(content)
                        if doc is None or doc.findtext('.//appmsg/type')!='57': continue
                        content=doc.findtext('.//appmsg/title') or ''
                        reference=doc.findtext('.//refermsg/svrid')
                        reference='s:'+reference if reference and reference.isdigit() else None
                    if account not in mentions:
                        # Inspect only the current text/title, never quoted text or XML metadata.
                        if row['create_time']<text_since or not re.search(r'[@＠]Openbot(?![A-Za-z0-9_])',content,re.IGNORECASE): continue
                        mentions=(*mentions,account)
                    ident='s:'+str(row['server_id']) if row['server_id'] else f'l:{path.stem}:{row["local_id"]}:{row["create_time"]}'
                    reference_data=resolve_reference(reader,config,reference) if reference else previous_image(reader,config,row,content)
                    if not reference and reference_data:reference=reference_data['message_id']
                    messages.append(dict(account=account,group=group,message_id=ident,
                        sender=row['sender_name'],mentions=mentions,text=content,
                        created=row['create_time'],reference_id=reference,
                        reference_data=reference_data))
                except (ValueError,TypeError,UnicodeError,OverflowError):
                    config['unreadable_messages']=config.get('unreadable_messages',0)+1
                    continue
        finally: db.close()
    return config, sorted(messages,key=lambda m:(m['created'],m['message_id']))


def text_and_reference(row):
    kind=row['local_type']&0xffffffff
    text=decode(row['message_content']);reference=None
    if kind==49:
        start=text.find('<msg')
        doc=xml(text[start:]) if start>=0 else None
        if doc is None or doc.findtext('.//appmsg/type')!='57': return None,None
        text=doc.findtext('.//appmsg/title') or ''
        ref=doc.findtext('.//refermsg/svrid')
        reference='s:'+ref if ref and ref.isdigit() else None
    elif kind!=1: return None,None
    return text,reference

def source_revoked(source):
    if (source['local_type']&0xffffffff) not in (10000,10002): return False
    raw=decode(source['message_content'])
    return '撤回' in raw or 'revokemsg' in raw.lower()


def quote_info(request):
    from media_reader import row_for
    config=json.loads((BASE/'runtime/account.json').read_text())
    if (request.get('account'),request.get('group'))!=(config['account'],config['group']): raise ValueError('scope_mismatch')
    reader=Reader();source=row_for(reader,config,request.get('message_id'))
    # WeChat can replace a withdrawn message in place, keeping its server ID.
    # This is a terminal source event, not a transient OCR/clipboard failure.
    if source_revoked(source):
        return {'unique':False,'latest_match':False,'source_state':'revoked'}
    text,_=text_and_reference(source)
    if not text or source['sender']==config['account']:
        return {'unique':False,'latest_match':False,'source_state':'unsupported'}
    text=text.removeprefix(source['sender']+':\n')
    table='Msg_'+hashlib.md5(config['group'].encode()).hexdigest(); matches={}
    for p in (reader.root/'message').glob('message_*.db'):
        if not re.fullmatch(r'message_\d+\.db',p.name): continue
        db=reader.snapshot('message/'+p.name)
        try:
            if not db.execute('SELECT 1 FROM sqlite_master WHERE name=?',(table,)).fetchone(): continue
            for row in db.execute(f'SELECT m.*,n.user_name AS sender FROM {table} m JOIN Name2Id n ON n.rowid=m.real_sender_id'):
                candidate,_=text_and_reference(row)
                if candidate and candidate.removeprefix(row['sender']+':\n')==text:
                    ident='s:'+str(row['server_id']) if row['server_id'] else f'l:{p.stem}:{row["local_id"]}:{row["create_time"]}'
                    matches[ident]=(row['create_time'],row['sort_seq'],row['local_id'])
        finally: db.close()
    newest=max(matches.values()) if matches else None
    latest=[ident for ident,key in matches.items() if key==newest]
    return {'unique':len(matches)==1,'latest_match':latest==[request.get('message_id')],'source_state':'available'}

def receipt(request):
    config=json.loads((BASE/'runtime/account.json').read_text())
    if request['account']!=config['account'] or request['group']!=config['group']:
        raise ValueError('Receipt scope mismatch')
    table='Msg_'+hashlib.md5(config['group'].encode()).hexdigest()
    reader=Reader(); matches=[]
    for path in sorted((reader.root/'message').glob('message_*.db')):
        if not re.fullmatch(r'message_\d+\.db',path.name): continue
        db=reader.snapshot('message/'+path.name)
        try:
            if not db.execute('SELECT 1 FROM sqlite_master WHERE name=?',(table,)).fetchone(): continue
            rows=db.execute(f'''SELECT m.* FROM {table} m JOIN Name2Id n ON n.rowid=m.real_sender_id
                WHERE n.user_name=? AND m.create_time>=? AND m.create_time<=? AND m.server_id!=0''',
                (config['account'],request['since'],request['since']+180)).fetchall()
            for row in rows:
                text,reference=text_and_reference(row)
                if text is None: continue
                prefix=config['account']+':\n'
                if text.startswith(prefix): text=text[len(prefix):]
                digest=hashlib.sha256(text.encode()).hexdigest()
                if digest==request['text_sha256']:
                    matches.append(dict(account=config['account'],group=config['group'],
                        message_id='s:'+str(row['server_id']),text_sha256=digest,reference_id=reference))
        finally: db.close()
    if len(matches)>1: raise RuntimeError('Ambiguous receipt')
    return matches[0] if matches else None


def watch():
    """Changes-only local pipe, independent of slow model/UI processing."""
    reader=None; previous=None; heartbeat=0; health_checked=0; failures=0
    while True:
        try:
            roots=database_roots()
            if len(roots)!=1: raise RuntimeError('Account not unique')
            if reader is None or reader.root!=roots[0]: reader=Reader(); previous=None
            paths=[BASE/'runtime/account.json',BASE/'runtime/text-mention.json',reader.root/'contact/contact.db',
                reader.root/'contact/contact.db-wal']
            paths+=sorted((reader.root/'message').glob('message_*.db*'))
            signature=[]
            for path in paths:
                if path.name.endswith('-shm'): continue
                try:
                    s=path.stat(); signature.append((str(path),s.st_ino,s.st_size,s.st_mtime_ns))
                except FileNotFoundError: signature.append((str(path),None))
            if time.monotonic()-health_checked>=5:
                from native_connection_health import status as connection_status
                connection=connection_status()
                if connection!='online':raise RuntimeError(connection)
                health_checked=time.monotonic()
            if signature!=previous:
                config,messages=read_recent(reader=reader)
                print(json.dumps({'kind':'batch','config':config,'messages':messages,
                    'decryptions':reader.decryptions},ensure_ascii=False),flush=True)
                previous=signature; heartbeat=time.monotonic()
            elif time.monotonic()-heartbeat>=5:
                print(json.dumps({'kind':'heartbeat','decryptions':reader.decryptions}),flush=True)
                heartbeat=time.monotonic()
            failures=0; time.sleep(.1)
        except BrokenPipeError: return
        except Exception as exc:
            failures+=1
            code=str(exc) if str(exc) in ('native_auth_required','native_reconnecting','native_transport_unavailable','native_target_changed') else 'snapshot_unavailable'
            print(json.dumps({'kind':'error','error_code':code}),flush=True)
            time.sleep(min(15,2**min(failures,4)))


if __name__=='__main__' and '--watch' in sys.argv:
    watch()
elif __name__=='__main__' and '--receipt' in sys.argv:
    print(json.dumps(receipt(json.load(sys.stdin))))
elif __name__=='__main__' and '--quote-info' in sys.argv:
    print(json.dumps(quote_info(json.load(sys.stdin))))
elif __name__=='__main__':
    config,messages=read_recent()
    if '--export' in sys.argv:
        print(json.dumps({'config':config,'messages':messages},ensure_ascii=False))
    else:
        print(json.dumps({'real_mentions':len(messages),'distinct_senders':len({m['sender'] for m in messages}),
            'reference_messages':sum(bool(m['reference_id']) for m in messages),
            'all_have_stable_ids':all(m['message_id'].startswith('s:') for m in messages)}))
