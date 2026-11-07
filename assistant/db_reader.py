"""Read-only, authenticated snapshots of this project's Linux WeChat databases.

Run inside WeChatAgent as root. Keys remain in its private runtime directory;
neither decrypted files nor original database modifications are made.
"""
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import struct
import subprocess
import time
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

BASE=Path('/opt/wechat-agent-v2')
CONTAINER='wechat-agent-v2-desktop-1'
PAGE=4096


def transport_config():
    path=BASE/'runtime/transport.json'
    return json.loads(path.read_text()) if path.exists() else {}

def database_roots(legacy=False):
    cfg=transport_config()
    home=Path(cfg['profile_home']) if cfg.get('mode')=='native' and not legacy else BASE/'data/wechat'
    return list((home/'xwechat_files').glob('*/db_storage'))

def native_online():
    cfg=transport_config()
    if cfg.get('mode')!='native':return True
    from native_gateway import verify
    return verify()


def checksum(data,seed=(0,0),endian='<'):
    a,b=seed
    words=struct.unpack(endian+str(len(data)//4)+'I',data)
    for i in range(0,len(words),2):
        a=(a+words[i]+b)&0xffffffff
        b=(b+words[i+1]+a)&0xffffffff
    return a,b


def committed_image(main,wal):
    if not wal: return main
    if len(wal)<32: raise ValueError('Incomplete WAL header')
    magic,version,size=struct.unpack('>III',wal[:12])
    if magic not in (0x377f0682,0x377f0683) or size!=PAGE:
        raise ValueError('Unsupported WAL')
    endian='<' if magic==0x377f0682 else '>'
    seed=checksum(wal[:24],endian=endian)
    if seed!=struct.unpack('>II',wal[24:32]): raise ValueError('WAL header checksum')
    frames=[]; committed=0; pages=0
    for start in range(32,len(wal)-PAGE-23,PAGE+24):
        head=wal[start:start+24]; payload=wal[start+24:start+24+PAGE]
        if head[8:16]!=wal[16:24]: break
        seed=checksum(head[:8]+payload,seed,endian)
        if seed!=struct.unpack('>II',head[16:24]): break
        number,size=struct.unpack('>II',head[:8])
        if number<1: break
        frames.append((number,payload))
        if size: committed=len(frames); pages=size
    if not committed: return main
    image=bytearray(main[:pages*PAGE])
    if len(image)<pages*PAGE: image.extend(b'\0'*(pages*PAGE-len(image)))
    for number,payload in frames[:committed]:
        if number<=pages: image[(number-1)*PAGE:number*PAGE]=payload
    return bytes(image)


def hmac_key(key,salt):
    return hashlib.pbkdf2_hmac('sha512',key,bytes(x^0x3a for x in salt),2,32)


def authentic(page,key,number=1,mac_key=None):
    mac_key=mac_key or hmac_key(key,page[:16])
    offset=16 if number==1 else 0
    actual=hmac.new(mac_key,page[offset:4032]+struct.pack('<I',number),'sha512').digest()
    return hmac.compare_digest(actual,page[4032:])


class Reader:
    def __init__(self,legacy=False):
        cfg=transport_config();self.native=cfg.get('mode')=='native' and not legacy
        self.native_keyfile=Path(cfg['keys_file']) if self.native else None
        accounts=database_roots(legacy)
        if len(accounts)!=1: raise RuntimeError('Expected exactly one project account')
        self.root=accounts[0]
        self.keyfile=BASE/'runtime/database-keys.json'
        self.keyfile.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        os.chmod(self.keyfile.parent,0o700)
        self.keys={}
        self.cache={}
        self.decryptions=0
        if self.native:
            self.refresh_native_keys()
        elif self.keyfile.exists():
            saved=json.loads(self.keyfile.read_text())
            if saved.get('account')==self.root.parent.name:
                self.keys=saved.get('keys',{})

    def refresh_native_keys(self):
        data=json.loads(self.native_keyfile.read_text())
        self.keys={d['relativePath']:base64.b64decode(d['rawPageKeyBase64']).hex() for d in data['databases']}
        self.native_image_key=data.get('imageDat')

    def path(self,relative):
        if not re.fullmatch(r'(contact/contact|session/session|message/message_\d+|message/message_resource|hardlink/hardlink|media/media_\d+)\.db',relative):
            raise ValueError('Database not allowed')
        return self.root/relative

    def discover_keys(self,relatives):
        if self.native:self.refresh_native_keys()
        pending={}
        for relative in relatives:
            with self.path(relative).open('rb') as source: pending[relative]=source.read(PAGE)
        for r,page in list(pending.items()):
            if r in self.keys and authentic(page,bytes.fromhex(self.keys[r])): del pending[r]
        if not pending: return
        if self.native:raise RuntimeError('native_database_key_unavailable')
        listing=subprocess.check_output(['docker','top',CONTAINER,'-eo','pid,comm'],text=True)
        pids=[line.split()[0] for line in listing.splitlines()[1:]
              if len(line.split())==2 and line.split()[1].lower()=='wechat']
        candidates=set()
        for pid in pids:
            maps=Path('/proc',pid,'maps').read_text().splitlines()
            with open(f'/proc/{pid}/mem','rb',buffering=0) as memory:
                for line in maps:
                    fields=line.split()
                    if 'rw' not in fields[1] or (len(fields)>5 and fields[5]!='[heap]'): continue
                    start,end=(int(x,16) for x in fields[0].split('-'))
                    carry=b''
                    for offset in range(start,end,1024*1024):
                        try:
                            memory.seek(offset); chunk=carry+memory.read(min(1024*1024,end-offset))
                        except OSError: break
                        for match in re.finditer(rb"x'([0-9a-fA-F]{64,192})'",chunk):
                            key=bytes.fromhex(match[1][:64].decode())
                            if key in candidates: continue
                            candidates.add(key)
                            for relative,page in list(pending.items()):
                                if authentic(page,key):
                                    self.keys[relative]=key.hex(); del pending[relative]
                        carry=chunk[-200:]
                        if not pending: break
                    if not pending: break
            if not pending: break
        if pending: raise RuntimeError('Database key unavailable')
        tmp=self.keyfile.with_suffix('.tmp')
        fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(fd,'w') as stream:
            json.dump({'account':self.root.parent.name,'keys':self.keys},stream)
        os.replace(tmp,self.keyfile); os.chmod(self.keyfile,0o600)

    def snapshot(self,relative):
        path=self.path(relative); wal=Path(str(path)+'-wal')
        def stamp(p):
            try:
                s=p.stat(); return s.st_ino,s.st_size,s.st_mtime_ns
            except FileNotFoundError: return None
        current=stamp(path),stamp(wal)
        cached=self.cache.get(relative)
        if cached and cached[0]==current:
            db=sqlite3.connect(':memory:'); db.deserialize(cached[1])
            db.execute('PRAGMA query_only=ON'); db.row_factory=sqlite3.Row
            return db
        self.discover_keys([relative])
        for _ in range(5):
            before=stamp(path),stamp(wal)
            main=path.read_bytes(); log=wal.read_bytes() if before[1] else b''
            if before==(stamp(path),stamp(wal)): break
            time.sleep(.05)
        else: raise RuntimeError('Database is changing; retry snapshot')
        encrypted=committed_image(main,log)
        key=bytes.fromhex(self.keys[relative]); mac=hmac_key(key,encrypted[:16])
        plain=bytearray()
        for start in range(0,len(encrypted),PAGE):
            page=encrypted[start:start+PAGE]; number=start//PAGE+1
            # Unallocated/free pages can be all zero and have no cipher MAC.
            # SQLite quick_check below still rejects a zero page used by a tree.
            if number>1 and len(page)==PAGE and not any(page):
                plain.extend(page); continue
            if len(page)!=PAGE or not authentic(page,key,number,mac):
                raise ValueError(f'Database authentication failed: page {number}, zero={not any(page)}')
            offset=16 if number==1 else 0
            decryptor=Cipher(algorithms.AES(key),modes.CBC(page[4016:4032])).decryptor()
            if number==1: plain.extend(b'SQLite format 3\0')
            plain.extend(decryptor.update(page[offset:4016])+decryptor.finalize()+b'\0'*80)
        plain[18:20]=b'\x01\x01'
        db=sqlite3.connect(':memory:'); db.deserialize(bytes(plain))
        db.execute('PRAGMA query_only=ON'); db.row_factory=sqlite3.Row
        if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':
            db.close(); raise ValueError('Snapshot SQLite integrity check failed')
        self.cache[relative]=(before,bytes(plain))
        self.decryptions+=1
        return db


if __name__=='__main__':
    reader=Reader()
    db=reader.snapshot('contact/contact.db')
    # Schema only, never dumps names, identifiers or conversations.
    print(json.dumps({'contact_snapshot':True,'columns':[
        row[1] for row in db.execute('PRAGMA table_info(contact)')]}))
    db.close()
