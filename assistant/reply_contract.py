"""Response schema and deterministic validation/formatting.

No model process, credential, filesystem or sending operations live here.
Legacy imports from codex_client remain supported.
"""
import json
import re
from copy import deepcopy
from evidence import has_verified_evidence,citation_report
from answer_limits import MAX_REPLY_CHARS, MAX_SOURCES, MAX_MEMORIES

ANSWER_SCHEMA={'type':'object','additionalProperties':False,
    'properties':{'category':{'type':'string','enum':['verified','unavailable','conversation','warning']},
        'model_disclosure':{'type':'boolean'},
        'answer':{'type':'string'},
        'unavailable_reason':{'type':'string','enum':['none','current_information','missing_material','missing_context','ambiguous_request']},
        'sources':{'type':'array','maxItems':MAX_SOURCES,'items':{
            'type':'object','additionalProperties':False,'properties':{
                'url':{'type':'string'},'as_of':{'type':'string'}},'required':['url','as_of']}},
        'memories':{'type':'array','maxItems':MAX_MEMORIES,'items':{'type':'object','additionalProperties':False,
            'properties':{k:{'type':'string'} for k in ('key','value','evidence')},
            'required':['key','value','evidence']}}},
    'required':['category','answer','model_disclosure','unavailable_reason','sources','memories']}


def source_schema(urls):
    """Restrict one repair's allowed citations without changing the shared schema."""
    schema=deepcopy(ANSWER_SCHEMA)
    schema['properties']['sources']['items']['properties']['url']['enum']=list(dict.fromkeys(urls))
    return schema


def compact_answer(text):
    lines=[];code=False
    for line in text.splitlines():
        if line.strip().startswith('```'):
            lines.append(line.strip());code=not code
        elif code:lines.append(line)
        elif line.strip():lines.append(re.sub(r'[^\S\n]+',' ',line).strip())
    return '\n'.join(lines)


def fit_reply(answer,footer=''):
    answer=compact_answer(answer)
    budget=MAX_REPLY_CHARS-len(footer)
    if budget<0:raise ValueError('Reply footer exceeds limit')
    if len(answer)<=budget: return answer+footer
    notice='\n（内容较长，先给出以上部分；可继续追问具体细节。）'
    # Reserve room for a closing code fence before selecting a natural boundary.
    limit=max(0,budget-len(notice)-4)
    prefix=answer[:limit]
    boundary=max(prefix.rfind(c)+1 for c in ('\n','。','！','？'))
    if boundary>=max(1,limit-400):prefix=prefix[:boundary]
    prefix=prefix.rstrip()
    if sum(line.strip().startswith('```') for line in prefix.splitlines())%2:
        prefix+='\n```'
    tail=notice if budget>=len(prefix)+len(notice) else ''
    return prefix+tail+footer


def material_status(media):
    """Distinguish attachment evidence from a mere text-reference ID.

    Documents arrive in notes (including nested materials), not image inputs.
    Notices/errors alone are not evidence that any content was obtained.
    """
    media=media or {}
    inputs=media.get('inputs') or []
    notes=media.get('notes') or []
    def has_document(items):
        for item in items:
            if not isinstance(item,dict) or item.get('unavailable'):continue
            document=item.get('document')
            if isinstance(document,str) and document.strip():return True
            if has_document(item.get('materials') or []):return True
        return False
    return {'material_expected':bool(inputs or notes),
            'media_available':bool(inputs) or has_document(notes)}


def public_model_info(model,effort=None):
    name='GPT-6 Astra（'+model+'）' if model=='gpt-6-astra' else model
    text='模型：OpenAI '+name+'。'
    if effort:text+='推理档位：'+effort+('（中档）' if effort=='medium' else '')+'。'
    return text+'通过 ChatGPT/Codex 订阅调用。'


def checked_reply(raw,events,checked_at,material_expected=False,model=None,include_sources=False,media_available=False,effort=None):
    data=json.loads(raw)
    if not isinstance(data,dict) or data.get('category') not in ANSWER_SCHEMA['properties']['category']['enum']:
        raise ValueError('Invalid answer category')
    if data.get('category')=='warning':
        warning='这部分涉及非公开的个人信息或敏感资料，我不能提供。'
        if data.get('model_disclosure') is True and model:warning=public_model_info(model,effort)+'\n'+warning
        return warning
    answer=data.get('answer','').strip()
    if not answer: raise ValueError('Empty answer')
    category=data.get('category')
    if category=='conversation':
        if not citation_report(data,events)['passed']:
            return '这次未能核对所引用的来源链接，暂时不能将这份带引用的答复作为已核实结果。请稍后重试。'
        return fit_reply(answer)
    if category=='unavailable':
        # Do not expose an unverified model answer that may contain stale claims.
        reason=data.get('unavailable_reason')
        if reason=='missing_context' or (reason=='missing_material' and not material_expected):
            return '我目前没有对应的先前聊天记录，无法确认。请引用那条消息再问。'
        if reason=='missing_material':
            if media_available:
                return '已取得本次引用材料，但这次未能可靠辨认或完成分析。请补充关键文字或更清晰的材料，我再继续分析。'
            return '这次未能取得可用的引用材料。请重新引用图片或文件并 @ 我，或贴出关键文字。'
        if reason=='ambiguous_request':
            return '目前的信息不足以确定你指的是哪个对象。请补充具体名称，或引用你要继续讨论的那条消息，我再接着回答。'
        return '这次未能核实最新信息，暂时无法给出可靠的实时答复。请稍后重试或补充具体对象、地区和时间。'
    sources=data.get('sources',[])
    if not has_verified_evidence(data,events) or not citation_report(data,events)['passed']:
        return '这次没有取得可核对的联网来源，不能把已有知识当成实时信息回答。请稍后重试。'
    if not include_sources:return fit_reply(answer)
    footer='\n来源：\n'
    for source in sources:
        line=source['url']+'\n'
        if len(footer)+len(line)>2200:
            footer+='（其余来源因消息长度省略）';break
        footer+=line
    return fit_reply(answer,footer.rstrip())
