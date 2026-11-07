"""Read the one authorized group's text window; no attachments or private chats."""
import hashlib
import json
import re
import sys
import time
from db_reader import Reader,BASE
from inbox import text_and_reference

def extract(reader,config,start,end):
    table='Msg_'+hashlib.md5(config['group'].encode()).hexdigest()
    messages={};excluded={k:set() for k in ('nontext','unreadable','assistant')}
    readers=[Reader(legacy=True),reader] if getattr(reader,'native',False) else [reader]
    for reader in readers:
        for path in sorted((reader.root/'message').glob('message_*.db')):
            if not re.fullmatch(r'message_\d+\.db',path.name):continue
            db=reader.snapshot('message/'+path.name)
            try:
                if not db.execute('SELECT 1 FROM sqlite_master WHERE name=?',(table,)).fetchone():continue
                rows=db.execute(f'''SELECT m.*,n.user_name AS sender FROM {table} m
                    LEFT JOIN Name2Id n ON n.rowid=m.real_sender_id
                    WHERE m.create_time>=? AND m.create_time<? ORDER BY m.create_time,m.sort_seq,m.local_id''',(start,end))
                for row in rows:
                    # A newer projection wins even when it is a recall/nontext
                    # or unreadable row: never retain the old plaintext copy.
                    if row['server_id']:
                        ident=str(row['server_id'])
                        messages.pop(ident,None)
                        for values in excluded.values():values.discard(ident)
                    if row['sender']==config['account']:excluded['assistant'].add(str(row['server_id']));continue
                    if not row['sender'] or not row['server_id']:excluded['unreadable'].add((str(path),row['local_id']));continue
                    try:text,_=text_and_reference(row)
                    except (ValueError,TypeError):excluded['unreadable'].add(str(row['server_id']));continue
                    if text is None:excluded['nontext'].add(str(row['server_id']));continue
                    text=text.removeprefix(row['sender']+':\n')
                    # Stable only within this summary window; no real nicknames/IDs.
                    speaker=hashlib.sha256((str(end)+row['sender']).encode()).hexdigest()[:8]
                    messages[str(row['server_id'])]={'speaker':speaker,'created':row['create_time'],'text':text}
            finally:db.close()
    counts={k:len(v) for k,v in excluded.items()}
    return {'start':start,'end':end,'messages':sorted(messages.values(),key=lambda x:x['created']),'excluded':counts}

def read(request):
    config=json.loads((BASE/'runtime/account.json').read_text())
    if (request.get('account'),request.get('group'))!=(config['account'],config['group']):raise ValueError('scope_mismatch')
    end=request.get('end')
    if not isinstance(end,int) or not time.time()-13*3600<=end<=time.time()+5:raise ValueError('invalid_window')
    reader=Reader()
    if not reader.root.parent.name.startswith(config['account']):raise ValueError('account_changed')
    return extract(reader,config,end-12*3600,end)

if __name__=='__main__':
    try:print(json.dumps(read(json.load(sys.stdin)),ensure_ascii=True))
    except Exception:print(json.dumps({'error':'group_window_unavailable'}));sys.exit(1)
