"""Prepare public X photos only. No sending, credentials, arbitrary URLs or files."""
import base64
import hashlib
import io
import json
from pathlib import Path
import re
import time
import urllib.request
from urllib.parse import urlsplit
from concurrent.futures import ThreadPoolExecutor
from PIL import Image

ROOT=Path(__file__).resolve().parent/'runtime'/'outbound-images'
MAX_BYTES=6*1024*1024
MAX_TOTAL=12*1024*1024
POST=re.compile(r'https://(?:x\.com|twitter\.com)/([A-Za-z0-9_]{1,15})/status/([1-9][0-9]{5,24})/?')

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ValueError('redirect_refused')

def get(url,limit):
    p=urlsplit(url)
    if (p.scheme!='https' or p.username or p.password or p.port or p.fragment
        or not ((p.hostname=='api.fxtwitter.com' and re.fullmatch(r'/[A-Za-z0-9_]{1,15}/status/[1-9][0-9]{5,24}',p.path) and not p.query)
        or (p.hostname=='api.fxtwitter.com' and re.fullmatch(r'/2/profile/[A-Za-z0-9_]{1,15}/media',p.path) and p.query=='count=3')
        or (p.hostname=='pbs.twimg.com' and re.fullmatch(r'/media/[A-Za-z0-9_-]+\.(jpg|png|webp)',p.path) and p.query in ('name=orig','format=jpg&name=orig')))):
        raise ValueError('unsupported_public_image_url')
    with urllib.request.build_opener(NoRedirect()).open(urllib.request.Request(url,headers={'User-Agent':'PublicImageReader/1.0'}),timeout=8) as r:
        data=r.read(limit+1)
    if not data or len(data)>limit:raise ValueError('image_size_limit')
    return data

def photo(url,root=ROOT,fetch=get):
    data=fetch(url,MAX_BYTES)
    with Image.open(io.BytesIO(data)) as im:
        if im.format not in ('JPEG','PNG','WEBP') or im.width*im.height>25_000_000 or getattr(im,'n_frames',1)!=1:
            raise ValueError('unsupported_image')
        im.load();width,height=im.size;fmt=im.format
        im.thumbnail((1024,1024));preview=io.BytesIO();im.convert('RGB').save(preview,'JPEG',quality=80)
    sha=hashlib.sha256(data).hexdigest();root.mkdir(parents=True,exist_ok=True)
    path=root/(sha+'.bin')
    if not path.exists():path.write_bytes(data)
    return {'sha256':sha,'bytes':len(data),'mime':{'JPEG':'image/jpeg','PNG':'image/png','WEBP':'image/webp'}[fmt],
        'width':width,'height':height,'image_url':url},'data:image/jpeg;base64,'+base64.b64encode(preview.getvalue()).decode()

def prepare(post_url,root=ROOT,fetch=get):
    match=POST.fullmatch(post_url)
    if not match:raise ValueError('invalid_post_url')
    handle,ident=match.groups();api=f'https://api.fxtwitter.com/{handle}/status/{ident}'
    doc=json.loads(fetch(api,600_000));post=doc.get('tweet') or {}
    if (doc.get('code')!=200 or str(post.get('id'))!=ident or
        (post.get('author') or {}).get('screen_name','').lower()!=handle.lower() or
        (post.get('author') or {}).get('protected') is not False or not POST.fullmatch(post.get('url',''))
        or POST.fullmatch(post['url']).groups()[1]!=ident
        or POST.fullmatch(post['url']).groups()[0].lower()!=handle.lower()):raise ValueError('post_identity_mismatch')
    photos=(post.get('media') or {}).get('photos') or []
    if not 1<=len(photos)<=4:raise ValueError('no_supported_photos')
    with ThreadPoolExecutor(max_workers=4) as pool:
        prepared=list(pool.map(lambda p:photo(p['url'],root,fetch),photos))
    assets=[{**a,'post_url':f'https://x.com/{handle}/status/{ident}','source_url':api} for a,_ in prepared]
    if sum(a['bytes'] for a in assets)>MAX_TOTAL:raise ValueError('image_size_limit')
    return {'source_url':api,'post_url':assets[0]['post_url'],'author':handle,'text':post.get('text','')[:6000],
        'posted_at':post.get('created_at'),'retrieved_at':time.time(),'photos':assets,
        'notice':'Public third-party mirror; not proof of latest X post. Photos prepared, NOT sent.'},[p for _,p in prepared]

def latest(handle,root=ROOT,fetch=get):
    if not re.fullmatch('[A-Za-z0-9_]{1,15}',handle):raise ValueError('invalid_handle')
    doc=json.loads(fetch(f'https://api.fxtwitter.com/2/profile/{handle}/media?count=3',600_000))
    if doc.get('code')!=200:raise ValueError('timeline_unavailable')
    posts=[p for p in doc.get('results',[])[:3] if (p.get('author') or {}).get('screen_name','').lower()==handle.lower()
        and (p.get('media') or {}).get('photos') and type(p.get('created_timestamp')) is int]
    if not posts:raise ValueError('no_recent_photos')
    post=max(posts,key=lambda p:p['created_timestamp'])
    result,previews=prepare(post['url'],root,fetch)
    result['latest_scope']='Newest own photo post in the three returned public media entries; mirror can lag; not guaranteed absolute latest.'
    return result,previews

def attachments(value):
    items=json.loads(value or '[]') if isinstance(value,(str,type(None))) else value
    if not isinstance(items,list) or len(items)>4:raise ValueError('invalid_attachments')
    for a in items:
        if (not isinstance(a,dict) or not re.fullmatch('[0-9a-f]{64}',a.get('sha256',''))
            or type(a.get('bytes')) is not int or not 0<a['bytes']<=MAX_BYTES
            or a.get('mime') not in ('image/jpeg','image/png','image/webp')
            or not POST.fullmatch(a.get('post_url',''))):raise ValueError('invalid_attachment')
    if sum(a['bytes'] for a in items)>MAX_TOTAL or len({a['sha256'] for a in items})!=len(items):raise ValueError('invalid_attachments')
    return items

def wire(items,root=ROOT):
    result=[]
    for a in attachments(items):
        data=(root/(a['sha256']+'.bin')).read_bytes()
        if len(data)!=a['bytes'] or hashlib.sha256(data).hexdigest()!=a['sha256']:raise ValueError('image_changed')
        result.append({**a,'data':base64.b64encode(data).decode()})
    return result

def receipt_matches(job,receipt):
    expected=attachments(job.get('attachments'))
    got=receipt.get('images',[]) if receipt else []
    return (len(expected)==len(got) and all(a['sha256']==r.get('sha256') and r.get('message_id') for a,r in zip(expected,got)))

def source_url(event):
    url=event.get('url','')
    if (event.get('type')=='publicImageRead' and event.get('success') is True
        and type(event.get('count')) is int and 1<=event['count']<=4
        and re.fullmatch(r'https://api\.fxtwitter\.com/[A-Za-z0-9_]{1,15}/status/[1-9][0-9]{5,24}',url)):return url

TOOL={'type':'namespace','name':'public_images','description':'Prepare original public X photos for this group reply. No send occurs during preparation.',
    'tools':[{'type':'function','name':'latest_x_photos','description':'For a request to show/send latest images from a known public X account: read current media timeline then prepare latest own photo post with visual previews. Prefer this over web search when handle is known (Qwen official: Alibaba_Qwen). Not for private people/member lookup.',
    'inputSchema':{'type':'object','properties':{'handle':{'type':'string'}},'required':['handle'],'additionalProperties':False}},
    {'type':'function','name':'prepare_x_photos','description':'Only when the current user requests images sent/shown. First find the correct public X post; this reads author, time and original photos and returns visual previews. Never use private/member data. One post per reply, up to four photos.',
    'inputSchema':{'type':'object','properties':{'post_url':{'type':'string'}},'required':['post_url'],'additionalProperties':False}}]}
