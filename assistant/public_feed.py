"""Bounded public archive adapter. No login, user text, arbitrary URLs or cache."""
import concurrent.futures
from datetime import datetime,timezone
import json
import re
import time
import urllib.request

BASE='https://elonmuskarchive.org'
LATEST=BASE+'/agents/latest?type=posts&limit=3'
TOOL={'type':'namespace','name':'public_feed','description':'Read-only live public social archives. Third-party coverage, not official complete timelines.',
 'tools':[{'type':'function','name':'latest_elon_posts',
 'description':'Fetch current latest Elon Musk X posts in one request, with original text, dates, first-post display time and source receipts. Prefer over web search for his latest X post. No cached answer. Archive may lag X; other people or older ranges require web.',
 'inputSchema':{'type':'object','properties':{},'additionalProperties':False}}]}

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

def fetch(path,timeout=3):
    # Only exact provider endpoints constructed here can cross the network.
    if not (path=='/agents/latest?type=posts&limit=3' or re.fullmatch(r'/agents/transcript/x-\d{10,25}',path)
            or re.fullmatch(r'/posts/\d{10,25}',path)):raise ValueError('invalid_feed_path')
    req=urllib.request.Request(BASE+path,headers={'Accept':'application/json,text/html','User-Agent':'WeChatResearch/1.0'})
    with urllib.request.build_opener(NoRedirect).open(req,timeout=timeout) as response:
        raw=response.read(600001)
        if len(raw)>600000:raise ValueError('feed_too_large')
        return raw.decode('utf-8')

def latest(fetcher=fetch):
    started=time.monotonic()
    data=json.loads(fetcher('/agents/latest?type=posts&limit=3'))
    entries=data.get('entries',[])
    if not isinstance(entries,list) or not entries:raise ValueError('feed_empty')
    selected=[]
    for entry in entries[:3]:
        ident=entry.get('id','')
        if not re.fullmatch(r'x-\d{10,25}',ident) or entry.get('type')!='post':raise ValueError('feed_invalid')
        if entry.get('source')!='https://x.com/elonmusk/status/'+ident[2:]:raise ValueError('feed_author_mismatch')
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',entry.get('date','')):raise ValueError('feed_date_missing')
        selected.append(entry)
    # Keep the provider's documented newest-first API order, not website pins.
    def detail(entry):
        d=json.loads(fetcher('/agents/transcript/'+entry['id']))
        if (d.get('id')!=entry['id'] or d.get('url')!=entry['source'] or d.get('type')!='post'
            or d.get('date')!=entry['date']):raise ValueError('feed_detail_mismatch')
        text=d.get('text')
        if not isinstance(text,str) or not text.strip() or len(text)>12000:raise ValueError('feed_text_invalid')
        return {'date':entry['date'],'text':text,'original_url':entry['source'],
                'archive_url':BASE+'/posts/'+entry['id'][2:],'detail_source':BASE+'/agents/transcript/'+entry['id']}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures=[pool.submit(detail,e) for e in selected]
        page=pool.submit(fetcher,'/posts/'+selected[0]['id'][2:])
        posts=[f.result() for f in futures]
        try:
            m=re.search(r'(\d{1,2}:\d{2}\s*[AP]M)\s*·\s*([A-Za-z]+\s+\d{1,2},\s+\d{4})',page.result())
            if m:posts[0]['display_time']=m.group(1)+' '+m.group(2)
        except Exception:pass  # Date/text remain supported by structured endpoints.
    return {'provider':'Elon Musk Archive (third-party)','account':'@elonmusk',
            'retrieved_at':datetime.now(timezone.utc).isoformat(),'source_url':LATEST,
            'posts':posts,'timezone':'not specified by archive',
            'coverage':'Latest entries returned by this archive, not guaranteed latest on X. Text can include referenced material; do not invent missing conversation context.',
            'elapsed_seconds':round(time.monotonic()-started,3)}

def receipt(result):
    # This receipt is constructed by local tool execution, never by model text.
    return {'type':'publicFeedRead','success':True,'url':LATEST,'count':len(result['posts'])}

def source_url(event):
    return LATEST if (event.get('type')=='publicFeedRead' and event.get('success') is True
                     and event.get('url')==LATEST and type(event.get('count')) is int
                     and 1<=event['count']<=3) else None
