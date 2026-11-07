"""Bounded primary-page reads for source repair, never arbitrary browsing."""
import concurrent.futures
import hashlib
import re
import urllib.request
from urllib.parse import urlsplit
from lxml import html

HOSTS=frozenset(('openai.com','www.openai.com','www.frontiermodelforum.org','frontiermodelforum.org',
    'research.meta.ai','www.nhs.uk','www.cdc.gov','www.weather.com.cn','weather.com.cn'))

def allowed(url):
    try:
        p=urlsplit(url)
        return (p.scheme=='https' and p.hostname in HOSTS and not p.username and not p.password
            and not p.port and not p.fragment and not p.query and len(url)<1500
            and not any(c.isspace() for c in url) and not p.path.lower().endswith('.pdf'))
    except (ValueError,TypeError):return False

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ValueError('redirect_refused')

def read(url):
    if not allowed(url):raise ValueError('unsupported_page')
    req=urllib.request.Request(url,headers={'User-Agent':'PublicResearch/1.0','Accept':'text/html'})
    with urllib.request.build_opener(NoRedirect()).open(req,timeout=4) as r:
        if r.headers.get_content_type() not in ('text/html','application/xhtml+xml'):raise ValueError('unsupported_content')
        data=r.read(1_000_001)
    if not 100<len(data)<=1_000_000:raise ValueError('page_size_limit')
    root=html.fromstring(data)
    for element in root.xpath('//script|//style|//nav|//footer|//header|//noscript|//form'):element.drop_tree()
    nodes=root.xpath('//main|//article');node=nodes[0] if nodes else root
    text=re.sub(r'\s+',' ',node.text_content()).strip()
    if len(text)<300 or any(s in text[:700].lower() for s in ('just a moment','access denied','verify you are human','enable javascript and cookies')):
        raise ValueError('page_unreadable')
    return {'url':url,'text':text[:18000],'truncated':len(text)>18000,'sha256':hashlib.sha256(data).hexdigest()}

def read_many(urls,reader=read):
    selected=list(dict.fromkeys(u for u in urls if allowed(u)))[:3]
    def safe(url):
        try:return reader(url)
        except Exception:return None
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        return [r for r in pool.map(safe,selected) if r]

def receipt(page):return {'type':'publicPageRead','success':True,'url':page['url'],'sha256':page['sha256']}

def source_url(event):
    if (event.get('type')=='publicPageRead' and event.get('success') is True and allowed(event.get('url',''))
        and re.fullmatch('[0-9a-f]{64}',event.get('sha256',''))):return event['url']
