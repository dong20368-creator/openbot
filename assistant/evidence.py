"""Observed web pages and successful read-only Futu receipts; no model or UI calls."""
import json
import re
from answer_limits import MAX_SOURCES
from urllib.parse import urlsplit
import ipaddress
from futu_evidence import TOOLS as FUTU_TOOLS,source_url as futu_source_url
from public_feed import source_url as public_feed_source_url
from outbound_images import source_url as image_source_url
from public_pages import source_url as page_source_url
from public_research import source_url as research_source_url

def canonical_url(value):
    if not isinstance(value,str) or len(value)>1500 or any(c.isspace() for c in value): return None
    # Internal provenance identifier, never a claim that a web page was opened.
    if value.startswith('futu://'):
        tool=value.removeprefix('futu://')
        return ('futu',tool,None,'/','') if tool in FUTU_TOOLS else None
    try:
        url=urlsplit(value);host=url.hostname
        if url.scheme not in ('https','http') or not host or url.username or url.password: return None
        if host=='localhost' or host.endswith(('.local','.localhost')): return None
        try:
            if not ipaddress.ip_address(host).is_global: return None
        except ValueError: pass
        return (url.scheme.lower(),host.lower(),url.port,url.path or '/',url.query)
    except ValueError: return None

def evidence_urls(events):
    """Web open/find or validated Futu receipt IDs; never search-result URLs."""
    found=set()
    for event in events:
        futu=futu_source_url(event) or public_feed_source_url(event) or image_source_url(event) or page_source_url(event) or research_source_url(event)
        if futu:
            found.add(canonical_url(futu));continue
        action=event.get('action') or {}
        if event.get('status') in ('failed','error','cancelled') or event.get('error'): continue
        if action.get('type') not in ('openPage','findInPage'): continue
        url=canonical_url(action.get('url'))
        if url: found.add(url)
    return found

def has_verified_evidence(data,events):
    return evidence_report(data,events)['passed']


def answer_links(data):
    # Citation links in prose, excluding literal code examples.
    text=re.sub(r'```.*?```|`[^`\n]*`','',data.get('answer',''),flags=re.S)
    return list(dict.fromkeys(re.findall(r'\[[^\]\n]*\]\((https?://[^\s)]+)\)',text)))


def citation_urls(events):
    found=evidence_urls(events)
    for event in events:
        if not (image_source_url(event) or research_source_url(event) or public_feed_source_url(event)):continue
        # These identifiers are recorded by successful local tool execution,
        # never extracted from an answer or accepted from a failed request.
        for url in event.get('original_urls',[]):
            if isinstance(url,str) and re.fullmatch(r'https://(?:x\.com|twitter\.com)/[A-Za-z0-9_]{1,15}/status/[1-9][0-9]{5,24}',url):
                found.add(canonical_url(url))
    return found


def citation_report(data,events):
    """Stable explanations with citations still require actual page reads."""
    links=answer_links(data)
    required=data.get('category')=='verified' or (data.get('category')=='conversation' and bool(data.get('sources') or links))
    if not required:return {'required':False,'passed':True,'reasons':[],'unmatched_body_count':0}
    report=evidence_report({**data,'category':'verified'},events)
    reasons=list(report['reasons'])
    # A stable explanation may cite directly in prose without a sources array.
    # There are then no dynamic data dates to validate, but every link must open.
    if data.get('category')=='conversation' and not data.get('sources') and 1<=len(links)<=MAX_SOURCES:
        reasons=[r for r in reasons if r!='source_count']
    observed=citation_urls(events)
    unmatched=sum(canonical_url(url) not in observed for url in links)
    if unmatched:reasons.append('unopened_body_link')
    return {'required':True,'passed':not reasons,'reasons':reasons,'unmatched_body_count':unmatched}


def evidence_report(data,events):
    """Counts/reasons can be persisted without storing source URLs or answers."""
    sources=data.get('sources',[]);observed=evidence_urls(events)
    if not isinstance(sources,list):sources=[]
    invalid=sum(not isinstance(s,dict) or canonical_url(s.get('url')) is None for s in sources)
    undated=sum(not isinstance(s,dict) or not isinstance(s.get('as_of'),str) or not s['as_of'].strip() for s in sources)
    unmatched=sum(isinstance(s,dict) and canonical_url(s.get('url')) not in observed for s in sources)
    reasons=[]
    if data.get('category')!='verified':reasons.append('not_external_verified')
    if not 1<=len(sources)<=MAX_SOURCES:reasons.append('source_count')
    if not observed:reasons.append('no_opened_source')
    if invalid:reasons.append('invalid_url')
    if undated:reasons.append('missing_data_time')
    if unmatched:reasons.append('unopened_source')
    return {'passed':not reasons,'required':data.get('category')=='verified','source_count':len(sources),'opened_count':len(observed),
        'unmatched_count':unmatched,'invalid_count':invalid,'undated_count':undated,'reasons':reasons}


def evidence_feedback(data,events):
    """Give correction the exact observed addresses, never infer URL aliases."""
    observed=evidence_urls(events)
    opened=list(dict.fromkeys(e['action']['url'] for e in events
        if (e.get('action') or {}).get('type') in ('openPage','findInPage')
        and not e.get('error') and e.get('status') not in ('failed','error','cancelled')
        and canonical_url(e['action'].get('url')) in observed))
    opened+=list(dict.fromkeys(url for e in events if (url:=futu_source_url(e))))
    opened+=list(dict.fromkeys(url for e in events if (url:=public_feed_source_url(e))))
    opened+=list(dict.fromkeys(url for e in events if (url:=image_source_url(e))))
    opened+=list(dict.fromkeys(url for e in events if (url:=page_source_url(e))))
    opened+=list(dict.fromkeys(url for e in events if (url:=research_source_url(e))))
    unmatched=[s.get('url') for s in data.get('sources',[]) if isinstance(s,dict)
        and canonical_url(s.get('url')) not in observed]
    return json.dumps({'核验结果':evidence_report(data,events),'引用核验':citation_report(data,events),
        '正文未匹配的引用链接':[url for url in answer_links(data) if canonical_url(url) not in citation_urls(events)],
        '本次可核对的实际访问网址':opened,'尚未匹配的网址':unmatched,
        '富途来源说明':'futu://标识代表本次成功且非空的只读MCP回执，不是网页，不能用web打开；事实必须由对应工具数据支持。',
        '说明':f'sources最多{MAX_SOURCES}项，逐项用实际打开且确实支持正文的完整网址；若需要另一个网址就先打开它。不得只改来源字段来保留无依据事实。'},ensure_ascii=False)
