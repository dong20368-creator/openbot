"""Provenance for successful, nonempty reads from the pinned Futu MCP server."""
import json

TOOLS=frozenset(('quote_stock_quote','quote_market_snapshot','quote_cur_kline','quote_history_kline',
                 'quote_financials_statements','quote_financials_revenue_breakdown',
                 'quote_economic_calendar_hot','quote_economic_calendar_search'))


def source_url(item):
    if (item.get('type')!='mcpToolCall' or item.get('server')!='futu_research'
        or item.get('tool') not in TOOLS or item.get('status')!='completed' or item.get('error')
        or item.get('readOnlyHint') is False):return None
    result=item.get('result')
    if not isinstance(result,dict) or result.get('isError'):return None
    payloads=[]
    if isinstance(result.get('structuredContent'),dict):payloads.append(result['structuredContent'])
    content=result.get('content') or []
    if not isinstance(content,list):return None
    for part in content:
        if isinstance(part,dict) and part.get('type')=='text':
            try:payloads.append(json.loads(part.get('text','')))
            except (ValueError,TypeError):pass
    good=False
    for payload in payloads:
        if not isinstance(payload,dict):continue
        if 'ret_code' in payload:
            if type(payload['ret_code']) is not int or payload['ret_code']!=0:return None
            data=payload.get('data')
            if isinstance(data,dict) and any(isinstance(v,list) and len(v)>0 for v in data.values()):good=True
    return 'futu://'+item['tool'] if good else None
