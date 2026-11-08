"""Bounded public news and X timeline reads. No login, disk cache or sending."""
from datetime import datetime, timezone
import hashlib
import json
import re
import time
import urllib.request
from answer_limits import MAX_RESEARCH_REQUESTS
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode, urlsplit, parse_qs

NEWS = 'https://ai-news-search.futunn.com/news_search'
X = 'https://api.fxtwitter.com'
HANDLE = r'[A-Za-z0-9_]{1,15}'
LIMIT = 1_000_000
POST_URL = re.compile(r'https://(?:x\.com|twitter\.com)/(' + HANDLE + r')/status/([1-9][0-9]{5,24})/?')


def valid_url(url):
    if not isinstance(url, str):
        return False
    try:
        p = urlsplit(url)
        if p.scheme != 'https' or p.username or p.password or p.port or p.fragment:
            return False
        q = parse_qs(p.query, strict_parsing=True)
        if any(len(v) != 1 for v in q.values()):
            return False
        if p.hostname == 'api.fxtwitter.com' and not p.query and re.fullmatch('/' + HANDLE + r'/status/[1-9][0-9]{5,24}', p.path):
            return True
        if p.hostname == 'ai-news-search.futunn.com' and p.path == '/news_search':
            return (set(q) == {'keyword', 'size', 'news_type', 'lang', 'sort_type'}
                    and bool(re.fullmatch(r'[\w .&-]{1,60}', q['keyword'][0]))
                    and q['size'] == ['5'] and q['news_type'][0] in ('1', '2', '3')
                    and q['lang'] == ['zh-CN'] and q['sort_type'] == ['2'])
        return (p.hostname == 'api.fxtwitter.com'
                and re.fullmatch('/2/profile/' + HANDLE + '/statuses', p.path) is not None
                and q == {'count': ['10'], 'with_replies': ['true']})
    except (ValueError, TypeError):
        return False


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('redirect_refused')


def fetch(url):
    if not valid_url(url):
        raise ValueError('invalid_public_endpoint')
    request = urllib.request.Request(url, headers={
        'User-Agent': 'PublicResearch/1.0', 'Accept': 'application/json'})
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=5) as response:
        if response.status != 200:
            raise ValueError('public_read_failed')
        raw = response.read(LIMIT + 1)
    if not raw or len(raw) > LIMIT:
        raise ValueError('public_size_limit')
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError('invalid_public_data')
    return data


def timestamp(value):
    if isinstance(value, bool):
        raise ValueError('invalid_timestamp')
    number = int(value)
    if not 946684800 <= number <= time.time() + 300:
        raise ValueError('invalid_timestamp')
    return number, datetime.fromtimestamp(number, timezone.utc).isoformat()


def package(kind, url, items, coverage, started):
    if not items:
        raise ValueError('public_data_empty')
    return {'kind': kind, 'source_url': url, 'items': items,
            'retrieved_at': datetime.now(timezone.utc).isoformat(), 'coverage': coverage,
            'elapsed_seconds': round(time.monotonic() - started, 3),
            'content_sha256': hashlib.sha256(json.dumps(items, ensure_ascii=False,
                sort_keys=True).encode()).hexdigest()}


def futu_news(keyword, news_type=1, fetcher=fetch):
    if (not isinstance(keyword, str) or not re.fullmatch(r'[\w .&-]{1,60}', keyword)
            or type(news_type) is not int or news_type not in (1, 2, 3)):
        raise ValueError('invalid_news_request')
    started = time.monotonic()
    url = NEWS + '?' + urlencode({'keyword': keyword, 'size': 5, 'news_type': news_type,
                                  'lang': 'zh-CN', 'sort_type': 2})
    data = fetcher(url)
    if type(data.get('code')) is not int or data['code'] != 0 or not isinstance(data.get('data'), list):
        raise ValueError('news_unavailable')
    items = []
    seen = set()
    for row in data['data'][:5]:
        if not isinstance(row, dict):
            continue
        title = row.get('title'); link = row.get('url', '')
        try:
            p = urlsplit(link)
            if (p.scheme != 'https' or p.hostname != 'news.futunn.com' or p.username
                    or p.password or p.port or not re.fullmatch(r'/post/\d+', p.path)):
                continue
            ts, date = timestamp(row.get('publish_time'))
        except (ValueError, TypeError, OverflowError):
            continue
        if not isinstance(title, str) or not title.strip() or len(title) > 1000 or '\ufffd' in title or p.path in seen:
            continue
        seen.add(p.path)
        items.append({'title': title, 'published_at': date, 'timestamp': ts,
                      'article_url': 'https://news.futunn.com' + p.path,
                      'full_text_read': False})
    items.sort(key=lambda r: r['timestamp'], reverse=True)
    return package('futu_news', url, items,
        'Five time-sorted search results, not exhaustive. Titles only; article_url is a candidate, '
        'NOT read evidence. For explanations, verify relevant original articles using web. '
        'Publication time is not necessarily event time. No quote/price data.', started)


def post(row, expected=None):
    if not isinstance(row, dict) or row.get('type') != 'status':
        return None
    author = row.get('author') or {}
    handle = author.get('screen_name', '')
    ident = str(row.get('id', ''))
    if (not re.fullmatch(HANDLE, handle) or author.get('protected') is not False
            or not re.fullmatch(r'[1-9][0-9]{5,24}', ident)
            or (expected and handle.lower() != expected.lower())):
        return None
    match = re.fullmatch(r'https://(?:x\.com|twitter\.com)/(' + HANDLE + r')/status/([1-9][0-9]{5,24})', row.get('url', ''))
    if not match or match[1].lower() != handle.lower() or match[2] != ident:
        return None
    text = row.get('text')
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        ts, date = timestamp(row.get('created_timestamp'))
    except (ValueError, TypeError, OverflowError):
        return None
    return {'id': ident, 'author': handle, 'text': text[:6000],
            'text_truncated': len(text) > 6000, 'published_at': date, 'timestamp': ts,
            'original_url': f'https://x.com/{handle}/status/{ident}',
            'is_reply': bool(row.get('replying_to')),
            'has_quote': bool(row.get('quote')), 'has_media': bool(row.get('media'))}


def latest_x_posts(handle, fetcher=fetch):
    if not isinstance(handle, str) or not re.fullmatch(HANDLE, handle):
        raise ValueError('invalid_handle')
    started = time.monotonic()
    url = X + '/2/profile/' + handle + '/statuses?count=10&with_replies=true'
    data = fetcher(url)
    if type(data.get('code')) is not int or data['code'] != 200 or not isinstance(data.get('results'), list):
        raise ValueError('timeline_unavailable')
    own = {}
    for row in data['results'][:100]:
        item = post(row, handle)
        if not item:
            continue
        # Reposts are not newly authored statements. A quote's old text is separate.
        if row.get('reposted_by'):
            continue
        quote = post(row.get('quote'))
        if quote:
            item['quoted_post'] = quote
        own[item['id']] = item
    items = sorted(own.values(), key=lambda r: r['timestamp'], reverse=True)[:5]
    return package('x_posts', url, items,
        'Newest own posts/replies found in ONE returned third-party timeline page, sorted by '
        'actual post time; pins do not imply recency. May lag X; NOT complete history or guaranteed '
        'absolute latest. Original URLs are identifiers; cite source_url for this read. '
        'Missing quote/reply context must not be invented. Media not downloaded or viewed.', started)


def run(name, args):
    if not isinstance(args, dict):
        raise ValueError('invalid_arguments')
    if name == 'futu_news' and set(args) == {'keyword', 'news_type'}:
        return futu_news(**args)
    if name == 'latest_x_posts' and set(args) == {'handle'}:
        return latest_x_posts(**args)
    if name == 'read_x_posts' and set(args) == {'post_urls'}:
        return read_x_posts(**args)
    raise ValueError('unsupported_research_request')


def read_x_posts(post_urls, fetcher=fetch):
    """Read up to three discovered public posts concurrently, not arbitrary URLs."""
    if (not isinstance(post_urls, list) or not 1 <= len(post_urls) <= 3
            or any(not isinstance(u, str) or not POST_URL.fullmatch(u) for u in post_urls)):
        raise ValueError('invalid_post_urls')
    urls = list(dict.fromkeys(post_urls))
    def read(url):
        started = time.monotonic()
        handle, ident = POST_URL.fullmatch(url).groups()
        api = f'{X}/{handle}/status/{ident}'
        try:
            data = fetcher(api)
            if type(data.get('code')) is not int or data['code'] != 200:
                raise ValueError('post_unavailable')
            raw = data.get('tweet')
            if not isinstance(raw, dict):
                raise ValueError('post_unavailable')
            item = post({**raw, 'type': 'status'}, handle)
            if not item or item['id'] != ident:
                raise ValueError('post_identity_mismatch')
            raw_quote = raw.get('quote')
            if isinstance(raw_quote, dict):
                quote = post({**raw_quote, 'type': 'status'})
                if quote:
                    item['quoted_post'] = quote
            result = package('x_post', api, [item],
                'One public post read through a third-party mirror. Author statement is an opinion, '
                'not independent verification of its claims. Quote text is separate. Missing reply context '
                'must not be invented. Images/video were NOT viewed. Cite source_url for read evidence.', started)
            return result, None
        except Exception:
            return None, {'post_url': url, 'error': 'post_unavailable'}
    with ThreadPoolExecutor(max_workers=3) as pool:
        rows = list(pool.map(read, urls))
    reads = [r for r, _ in rows if r]
    if not reads:
        raise ValueError('all_posts_unavailable')
    return {'kind': 'x_batch', 'reads': reads, 'failed': [e for _, e in rows if e],
            'coverage': 'Small discovered sample, NOT all X opinion or a representative poll. '
            'Use each successful read source_url. Failed URLs are not evidence.'}


def receipt(result):
    originals=[]
    if result.get('kind') in ('x_post','x_posts'):
        for item in result['items']:
            for post in (item,item.get('quoted_post',{})):
                url=post.get('original_url')
                if isinstance(url,str) and POST_URL.fullmatch(url):originals.append(url)
    return {'type': 'publicResearchRead', 'success': True, 'url': result['source_url'],
            'count': len(result['items']), 'content_sha256': result['content_sha256'],
            'original_urls':list(dict.fromkeys(originals))}


def source_url(event):
    url = event.get('url')
    if (event.get('type') == 'publicResearchRead' and event.get('success') is True
            and type(event.get('count')) is int and 1 <= event['count'] <= 5
            and isinstance(event.get('content_sha256'), str)
            and re.fullmatch('[0-9a-f]{64}', event['content_sha256']) and valid_url(url)):
        return url


# Discovery workflow belongs to the tool that uses it, not every task policy.
X_DISCOVERY_GUIDANCE = f'事件评论无需用户先给账号。首批web搜索合并权威事件结果、事件+reactions/on X/回应报道、site:x.com+事件日期；不用错误路径site:x.com/status。打开报道提取真实x.com/账号/status/数字链接，调用public_research.read_x_posts每次最多3条；与新闻和时间线共用最多{MAX_RESEARCH_REQUESTS}次新请求，已用额度不能重复分配。不猜ID，不因登录墙就放弃直读。\n首批不足再针对缺口补一批，不反复换日期格式搜同义词；只找到账号且其最新页可能覆盖目标时间时才读latest_x_posts，不能拿今天的高频账号列表找数天前旧帖或凑无关观点。原帖必须落在事件窗口，事前预测不是事后反应。'

TOOL = {'type': 'namespace', 'name': 'public_research',
        'description': 'Fast public structured sources. No account access or sends. Public companies/accounts only; never member identities or private context.',
        'tools': [
            {'type': 'function', 'name': 'futu_news',
             'description': 'First choice for company latest news/notices/research: one public Futu time-sorted list. Titles/time/links only; use web to read relevant article bodies for explanations. Keyword is ONLY public company name/ticker, not the user question.',
             'inputSchema': {'type': 'object', 'properties': {'keyword': {'type': 'string'},
                 'news_type': {'type': 'integer', 'enum': [1, 2, 3], 'description': '1 news, 2 notices, 3 research'}},
                 'required': ['keyword', 'news_type'], 'additionalProperties': False}},
            {'type': 'function', 'name': 'latest_x_posts',
             'description': 'First choice for known public X account latest statements (including replies), one current timeline page and full text. Not a date-range archive or global trends. Qwen: Alibaba_Qwen; Elon Musk: elonmusk. Only if latest_x_posts fails for Elon Musk, use public_feed.latest_elon_posts before web fallback. Does NOT prepare/send/view images.',
             'inputSchema': {'type': 'object', 'properties': {'handle': {'type': 'string'}},
                 'required': ['handle'], 'additionalProperties': False}},
            {'type': 'function', 'name': 'read_x_posts',
             'description': 'Read full text/author/time/quoted post for up to 3 public X post URLs in parallel. For event/topic reactions, first find real status URLs with web search, then use this instead of trying login-blocked X pages or relying only on media paraphrases. No image/file sends. Public topics only.' + '\n' + X_DISCOVERY_GUIDANCE,
             'inputSchema': {'type': 'object', 'properties': {'post_urls': {
                 'type': 'array', 'items': {'type': 'string'}, 'minItems': 1, 'maxItems': 3}},
                 'required': ['post_urls'], 'additionalProperties': False}}]}
