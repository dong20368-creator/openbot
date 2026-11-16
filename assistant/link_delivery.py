"""Separate external links from answer text with durable per-part identities.

Aggregate receipts describe verified parts, never recipient delivery.
"""
import hashlib
import re

URL = re.compile(r'https?://[^\s<>\[\]"\u3000]+', re.I)
MARKDOWN = re.compile(r'\[([^\]\n]*)\]\((https?://[^\s<>]+)\)', re.I)


def split_links(text):
    links = []

    def remember(url):
        if url not in links:
            links.append(url)
        return links.index(url) + 1

    def markdown(match):
        number = remember(match[2])
        label = match[1]
        return (label if not URL.search(label) else '') + f'〔{number}〕'

    body = MARKDOWN.sub(markdown, text)

    def bare(match):
        url = match[0].rstrip('.,;:!?，。；：！？）)')
        suffix = match[0][len(url):]
        return f'〔{remember(url)}〕' + suffix

    body = URL.sub(bare, body)
    if not links:
        return [text]
    # No truncation: the transport limit applies to each complete part.
    parts = []
    while len(body) > 4000:
        cut = body.rfind('\n', 0, 4000)
        cut = cut if cut > 2000 else 4000
        parts.append(body[:cut])
        body = body[cut:].lstrip('\n')
    parts.append(body)
    chunk = '来源链接（与正文分开发送）：'
    for number, url in enumerate(links, 1):
        line = f'\n〔{number}〕{url}'
        if len(chunk + line) > 4000:
            parts.append(chunk)
            chunk = ''
        if len(line) > 4000:
            raise ValueError('source_link_too_long')
        chunk += line
    if chunk:
        parts.append(chunk)
    return parts


def dispatch_parts(req, single):
    op = req['op']
    if op not in ('send', 'receipt'):
        return single(req)
    job = req['job']
    text = req.get('text') if op == 'send' else job.get('answer')
    if not isinstance(text, str):
        return single(req)
    parts = split_links(text)
    if len(parts) == 1:
        return single(req)
    # Preserve receipts from before this deployment; never replay an old send.
    legacy = single({'op': 'receipt', 'job': job})
    if legacy:
        return legacy
    original_hash = hashlib.sha256(text.encode()).hexdigest()
    if op == 'receipt' and original_hash != job.get('answer_hash'):
        raise RuntimeError('receipt_mismatch')
    receipts = []
    for index, part in enumerate(parts):
        part_hash = hashlib.sha256(part.encode()).hexdigest()
        child = {**job, 'id': f"{job['id']}:link-parts-v1:{index}",
                 'answer': part, 'answer_hash': part_hash}
        if index:
            child['attachments'] = None
        request = {'op': op, 'job': child}
        if op == 'send':
            request.update(text=part, images=req.get('images', []) if index == 0 else [])
        receipt = single(request)
        if receipt is None:
            return None
        if (receipt.get('account'), receipt.get('group'), receipt.get('text_sha256')) != (
                job['account'], job['room'], part_hash) or not receipt.get('message_id'):
            raise RuntimeError('receipt_mismatch')
        receipts.append(receipt)
    return {**receipts[0], 'text_sha256': original_hash,
            'evidence': 'multipart_server_acceptance_and_local_readback',
            'recipient_verified': False,
            'parts': [{'message_id': r['message_id'], 'text_sha256': r['text_sha256']}
                      for r in receipts]}
