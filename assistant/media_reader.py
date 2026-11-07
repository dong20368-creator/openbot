"""Linux: resolve only DB-bound media in the authorized group, never XML URLs."""
import base64
import hashlib
import json
import re
import struct
import sys
from datetime import datetime
from pathlib import Path
from cryptography.hazmat.primitives.ciphers import Cipher,algorithms,modes
from cryptography.hazmat.primitives.padding import PKCS7
from db_reader import Reader,BASE

MAX_BYTES=24*1024*1024

def image_dimensions(raw):
    """Read bounded image headers without a new Linux imaging dependency."""
    try:
        if raw.startswith(b'\x89PNG\r\n\x1a\n'):
            return struct.unpack_from('>II',raw,16)
        if raw.startswith((b'GIF87a',b'GIF89a')):
            return struct.unpack_from('<HH',raw,6)
        if raw[:4]==b'RIFF' and raw[8:12]==b'WEBP':
            if raw[12:16]==b'VP8X':
                return 1+int.from_bytes(raw[24:27],'little'),1+int.from_bytes(raw[27:30],'little')
            if raw[12:16]==b'VP8L' and raw[20]==0x2f:
                bits=int.from_bytes(raw[21:25],'little')
                return (bits&0x3fff)+1,((bits>>14)&0x3fff)+1
            if raw[12:16]==b'VP8 ' and raw[23:26]==b'\x9d\x01\x2a':
                w,h=struct.unpack_from('<HH',raw,26);return w&0x3fff,h&0x3fff
        if raw.startswith(b'\xff\xd8'):
            pos=2
            while pos<len(raw):
                if raw[pos]!=255:return None
                while pos<len(raw) and raw[pos]==255:pos+=1
                marker=raw[pos];pos+=1
                if marker in (0xd9,0xda):break
                if marker==1 or 0xd0<=marker<=0xd8:continue
                length=struct.unpack_from('>H',raw,pos)[0]
                if length<2 or pos+length>len(raw):return None
                if marker in (0xc0,0xc1,0xc2,0xc3,0xc5,0xc6,0xc7,0xc9,0xca,0xcb,0xcd,0xce,0xcf):
                    h,w=struct.unpack_from('>HH',raw,pos+3);return w,h
                pos+=length
    except (IndexError,struct.error):pass
    return None

def best_image(candidates,account,reader,config):
    best=None;score=(-1,-1);error='media_not_downloaded'
    for path in dict.fromkeys(candidates):
        if not path.resolve().is_relative_to(account) or not path.is_file():continue
        if not 0<path.stat().st_size<=MAX_BYTES:continue
        try:
            raw=decode_account_image(path.read_bytes(),reader,config)
            size=image_dimensions(raw)
            if size and (min(size)<=0 or size[0]*size[1]>25_000_000):
                raise ValueError('image_too_large')
            quality=(size[0]*size[1] if size else 0,len(raw))
            if quality>score:best=(path,raw,size);score=quality
        except ValueError as exc:error=str(exc)
    if best is None:raise ValueError(error)
    return best

def image_magic(data):
    return data.startswith((b'\xff\xd8\xff',b'\x89PNG',b'GIF8',b'wxgf')) or data[:4]==b'RIFF' and data[8:12]==b'WEBP'

def decode_image(raw,key=None,tail_key=None):
    if image_magic(raw): return raw
    if raw[:6] in (b'\x07\x08V1\x08\x07',b'\x07\x08V2\x08\x07'):
        if len(raw)<31: raise ValueError('incomplete_image')
        if raw[3:4]==b'1': key=b'cfcd208495d565ef'
        if not key: raise ValueError('image_key_unavailable')
        length,tail=struct.unpack_from('<II',raw,6)
        split=15+(length//16+1)*16
        if split>len(raw) or tail>len(raw)-split: raise ValueError('incomplete_image')
        cipher=Cipher(algorithms.AES(key),modes.ECB()).decryptor()
        head=cipher.update(raw[15:split])+cipher.finalize()
        unpad=PKCS7(128).unpadder(); head=unpad.update(head)+unpad.finalize()
        if len(head)!=length or not image_magic(head): raise ValueError('image_key_invalid')
        if tail and tail_key is None and head[:3]==b'\xff\xd8\xff' and tail>=2:
            tail_key=raw[-1]^0xd9
            if raw[-2]^tail_key!=0xff: tail_key=None
        if tail and tail_key is None: raise ValueError('image_tail_key_unavailable')
        return head+raw[split:len(raw)-tail]+bytes(b^tail_key for b in raw[len(raw)-tail:]) if tail else head+raw[split:]
    for magic in (b'\xff\xd8\xff',b'\x89PNG',b'GIF8',b'RIFF'):
        if raw:
            k=raw[0]^magic[0]
            if bytes(b^k for b in raw[:len(magic)])==magic: return bytes(b^k for b in raw)
    raise ValueError('unknown_image_format')

def decode_account_image(raw,reader,config):
    try: return decode_image(raw)
    except ValueError: pass
    if getattr(reader,'native',False) and reader.native_image_key:
        import base64
        k=reader.native_image_key
        return decode_image(raw,base64.b64decode(k['rawAes128KeyBase64']),k['xorByte'])
    # Linux key derivation candidates are accepted only after file validation.
    for stat in (BASE/'data/wechat/.xwechat/net/kvcomm').glob('*.statistic'):
        for value in stat.stem.removeprefix('key_').split('_')[:2]:
            if not value.isdigit(): continue
            for identity in (config['account'],reader.root.parent.name):
                key=hashlib.md5((value+identity).encode()).hexdigest()[:16].encode()
                try: return decode_image(raw,key,int(value)&255)
                except ValueError: pass
    raise ValueError('image_key_unavailable')

def row_for(reader,config,ident):
    if not isinstance(ident,str) or not re.fullmatch(r's:\d+',ident): raise ValueError('invalid_message')
    table='Msg_'+hashlib.md5(config['group'].encode()).hexdigest()
    for p in sorted((reader.root/'message').glob('message_*.db')):
        if not re.fullmatch(r'message_\d+\.db',p.name): continue
        db=reader.snapshot('message/'+p.name)
        try:
            if not db.execute('SELECT 1 FROM sqlite_master WHERE name=?',(table,)).fetchone(): continue
            row=db.execute(f'SELECT m.*,n.user_name AS sender FROM {table} m JOIN Name2Id n ON n.rowid=m.real_sender_id WHERE m.server_id=?',(int(ident[2:]),)).fetchone()
            if row: return dict(row)
        finally: db.close()
    if getattr(reader,'native',False):
        old=Reader(legacy=True)
        row=row_for(old,config,ident)
        reader.__dict__.update(old.__dict__)
        return row
    raise ValueError('message_unavailable')

def media_kind(row):
    from inbox import decode,xml
    kind=row['local_type']&0xffffffff
    if kind in (3,43,47): return {3:'image',43:'video',47:'sticker'}[kind]
    if kind==49:
        doc=xml(decode(row['message_content']).removeprefix(row['sender']+':\n'))
        if doc is not None and doc.findtext('.//appmsg/type')=='6': return 'file'
    return None

def read_media(request,reader=None):
    from inbox import decode
    config=json.loads((BASE/'runtime/account.json').read_text())
    if (request.get('account'),request.get('group'))!=(config['account'],config['group']): raise ValueError('scope_mismatch')
    reader=reader or Reader()
    if not reader.root.parent.name.startswith(config['account']): raise ValueError('account_changed')
    row=row_for(reader,config,request.get('message_id'))
    kind=media_kind(row)
    if not kind: raise ValueError('not_media')
    blobs=[decode(row['message_content']).encode()]
    blobs += [bytes(v) for k,v in row.items() if isinstance(v,(bytes,bytearray)) and k!='message_content']
    indices=[]
    db=reader.snapshot('message/message_resource.db')
    try:
        entries=db.execute('''SELECT i.packed_info AS info,d.packed_info AS detail,d.data_index FROM MessageResourceInfo i
            JOIN ChatName2Id c ON c.rowid=i.chat_id JOIN MessageResourceDetail d ON d.message_id=i.message_id
            WHERE c.user_name=? AND i.message_svr_id=?''',(config['group'],int(request['message_id'][2:]))).fetchall()
        for entry in entries:
            for name in ('info','detail'):
                if entry[name]: blobs.append(bytes(entry[name]))
            if entry['data_index']: indices.append(str(entry['data_index']))
    finally: db.close()
    hashes=list(dict.fromkeys(x.decode() for blob in blobs for x in re.findall(rb'(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])',blob)))
    account=reader.root.parent.resolve(); group_hash=hashlib.md5(config['group'].encode()).hexdigest()
    month=datetime.fromtimestamp(row['create_time']).strftime('%Y-%m')
    candidates=[]
    for index in indices:
        # data_index is local DB metadata, still never allow traversal or URLs.
        if re.fullmatch(r'[A-Za-z0-9_.-]{1,160}',index): candidates.append(account/'resource'/index)
    groupdirs=[account/'msg/attach'/group_hash/month,account/'cache'/month/'Message'/group_hash]
    for folder in groupdirs:
        if folder.is_dir():
            for p in folder.rglob('*'):
                if p.is_file() and any(h in p.name for h in hashes): candidates.append(p)
    if hashes:
        db=reader.snapshot('hardlink/hardlink.db')
        try:
            table={'image':'image','sticker':'image','video':'video'}.get(kind,'file')+'_hardlink_info_v4'
            for digest in hashes:
                rows=db.execute(f'''SELECT h.file_name,a.username AS dir1,b.username AS dir2 FROM {table} h
                    LEFT JOIN dir2id a ON a.rowid=h.dir1 LEFT JOIN dir2id b ON b.rowid=h.dir2 WHERE h.md5=?''',(digest,)).fetchall()
                for item in rows:
                    for base in ('msg/attach','msg/file','msg/video'):
                        parts=[item['dir1'] or '',item['dir2'] or '',item['file_name'] or '']
                        p=account/base/Path(*parts)
                        if p.resolve().is_relative_to(account): candidates.append(p)
        finally: db.close()
    if kind in ('image','sticker'):
        path,raw,size=best_image(candidates,account,reader,config)
        return {'kind':kind,'name':path.name,'data':base64.b64encode(raw).decode(),
            'sha256':hashlib.sha256(raw).hexdigest(),'message_id':request['message_id'],
            'dimensions':list(size) if size else None,'selection':'largest_pixel_area'}
    errors=[]
    for path in dict.fromkeys(candidates):
        if not path.resolve().is_relative_to(account) or not path.is_file(): continue
        if not 0<path.stat().st_size<=MAX_BYTES: continue
        raw=path.read_bytes()
        try:
            if kind in ('image','sticker'): raw=decode_account_image(raw,reader,config)
        except ValueError as exc: errors.append(str(exc)); continue
        name=path.name
        if kind=='file':
            from inbox import xml
            doc=xml(decode(row['message_content']).removeprefix(row['sender']+':\n'))
            if doc is not None: name=(doc.findtext('.//appmsg/title') or name)[:180]
        return {'kind':kind,'name':name,'data':base64.b64encode(raw).decode(),
            'sha256':hashlib.sha256(raw).hexdigest(),'message_id':request['message_id']}
    raise ValueError(errors[-1] if errors else 'media_not_downloaded')

if __name__=='__main__':
    try: print(json.dumps(read_media(json.load(sys.stdin))))
    except Exception as exc:
        print(json.dumps({'error':str(exc) if isinstance(exc,ValueError) else type(exc).__name__})); sys.exit(1)
