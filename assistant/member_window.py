"""Linux-only scoped public group statements, separate from personal memories."""
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from db_reader import Reader, BASE
from inbox import text_and_reference, decode, xml
from group_summary import scrub

TZ = timezone(timedelta(hours=8))


def members(reader, config):
    db = reader.snapshot('contact/contact.db')
    try:
        return [dict(r) for r in db.execute('''SELECT u.username,u.nick_name FROM chatroom_member m
            JOIN contact g ON m.room_id=g.id JOIN contact u ON m.member_id=u.id
            WHERE g.username=?''', (config['group'],))]
    finally:
        db.close()


def choose(member, roster, config, source, quoted_sender=None):
    ids = {r['username'] for r in roster} - {config['account']}
    if member == '@self':
        selected = {source['sender']}
    elif member == '@quoted':
        selected = {quoted_sender} if quoted_sender else set()
    elif member == '@mentioned':
        document = xml(decode(source.get('source')))
        selected = {v.strip() for t in document.iter('atuserlist') for v in (t.text or '').split(',')} if document is not None else set()
        selected.discard(config['account'])
    else:
        name = member.strip().removeprefix('@')
        selected = {r['username'] for r in roster if r.get('nick_name') == name}
    selected &= ids
    if len(selected) != 1:
        return None
    return selected.pop()


def extract(readers, config, target, start, end):
    table = 'Msg_' + hashlib.md5(config['group'].encode()).hexdigest()
    records = {}
    nontext = set()
    unreadable = set()
    scanned = 0
    for reader in readers:  # legacy first, current projection overrides tombstones too
        for path in sorted((reader.root/'message').glob('message_*.db')):
            if not re.fullmatch(r'message_\d+\.db', path.name):
                continue
            db = reader.snapshot('message/' + path.name)
            try:
                if not db.execute('SELECT 1 FROM sqlite_master WHERE name=?', (table,)).fetchone():
                    continue
                rows = db.execute(f'''SELECT m.*,n.user_name AS sender FROM {table} m
                    LEFT JOIN Name2Id n ON n.rowid=m.real_sender_id
                    WHERE m.create_time>=? AND m.create_time<? ORDER BY m.create_time,m.sort_seq,m.local_id''', (start,end))
                for row in rows:
                    scanned += 1
                    if scanned > 20000:
                        raise ValueError('window_too_large_use_shorter_range')
                    ident = str(row['server_id'])
                    if not row['server_id']:
                        continue
                    records.pop(ident, None); nontext.discard(ident); unreadable.discard(ident)
                    if row['sender'] != target:
                        continue
                    try:
                        text, reference = text_and_reference(row)
                        if text is None:
                            nontext.add(ident); continue
                        text = scrub(text.removeprefix(target + ':\n'))
                        if not text.strip():
                            unreadable.add(ident); continue
                    except (ValueError, TypeError, UnicodeError, OverflowError):
                        unreadable.add(ident); continue
                    records[ident] = {'source_message_id':'s:'+ident,'time':datetime.fromtimestamp(row['create_time'],TZ).isoformat(),
                        'text':text[:1500], 'text_truncated':len(text)>1500,
                        'is_reply':bool(reference),
                        'notice':'Only this member own reply text; quoted other text is NOT included.' if reference else ''}
            finally:
                db.close()
    ordered = sorted(records.values(), key=lambda r:r['time'])
    selected=[]; used=0
    for row in reversed(ordered[-200:]):
        cost=len(json.dumps(row,ensure_ascii=False))
        if used+cost>24000:
            break
        selected.append(row);used+=cost
    selected.reverse()
    return {'kind':'authorized_member_statements','start':datetime.fromtimestamp(start,TZ).isoformat(),
            'end':datetime.fromtimestamp(end,TZ).isoformat(),'records':selected,
            'matched_text_count':len(ordered),'omitted_for_size':len(ordered)-len(selected),
            'excluded_nontext':len(nontext),'redacted_or_unreadable':len(unreadable),
            'coverage':'Only locally synced text in this one group. Missing history is possible. '
                'No private chats, other groups, stored personal facts, bot answers, images/audio/video. '
                'Own reply text is separate from unavailable quoted context. No complete-history claim.'}


def read(request):
    config=json.loads((BASE/'runtime/account.json').read_text())
    if (request.get('account'),request.get('group')) != (config['account'],config['group']):
        raise ValueError('scope_mismatch')
    days=request.get('days');member=request.get('member');end=request.get('end')
    if (type(days) is not int or not 1<=days<=7 or not isinstance(member,str) or not 1<=len(member)<=80
            or type(end) is not int or not time.time()-1800<=end<=time.time()+5):
        raise ValueError('invalid_request')
    from media_reader import row_for
    # Source identity is resolved in the fixed authorized group, not supplied by model.
    source=row_for(Reader(),config,request.get('message_id'))
    if source['sender']!=request.get('sender'):
        raise ValueError('source_sender_mismatch')
    text,reference=text_and_reference(source)
    if text is None or abs(source['create_time']-end)>1:
        raise ValueError('source_changed')
    packed=xml(decode(source.get('source')))
    mentions={v.strip() for t in packed.iter('atuserlist') for v in (t.text or '').split(',')} if packed is not None else set()
    if config['account'] not in mentions:
        policy=BASE/'runtime/text-mention.json'
        enabled=json.loads(policy.read_text()).get('enabled_since',float('inf')) if policy.exists() else float('inf')
        if source['create_time']<enabled or not re.search(r'[@＠]Openbot(?![A-Za-z0-9_])',text,re.IGNORECASE):
            raise ValueError('not_mentioned')
    reader=Reader()
    if not reader.root.parent.name.startswith(config['account']):
        raise ValueError('account_changed')
    quoted=None
    if member=='@quoted' and reference:
        try:quoted=row_for(Reader(),config,reference)['sender']
        except (ValueError,RuntimeError):pass
    target=choose(member,members(reader,config),config,source,quoted)
    if not target:
        return {'status':'ambiguous_or_not_found', 'message':'请真正@目标成员或引用他的一条消息；重名不能猜，群昵称可能与账号昵称不同。'}
    readers=[];legacy_available=True
    if getattr(reader,'native',False):
        try:readers.append(Reader(legacy=True))
        except Exception:legacy_available=False
    readers.append(reader)
    result=extract(readers,config,target,end-days*86400,end)
    result.update(status='ok',target_id=target,legacy_available=legacy_available)
    return result


if __name__=='__main__':
    try:print(json.dumps(read(json.load(sys.stdin)),ensure_ascii=True))
    except Exception:print(json.dumps({'status':'unavailable','message':'本群指定成员的发言暂时不可读取；请勿猜测。'}))
