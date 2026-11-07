"""Bounded revisions of an unsent answer; never modify a delivered message."""
from copy import deepcopy
from answer_limits import MAX_REPLY_CHARS


def revision_schema(schema):
    result=deepcopy(schema)
    for key in ('answer','memories'):
        result['properties'].pop(key)
        result['required'].remove(key)
    result['properties']['answer_edits']={'type':'array','maxItems':12,'items':{
        'type':'object','additionalProperties':False,
        'properties':{k:{'type':'string','maxLength':MAX_REPLY_CHARS} for k in ('old','new')},
        'required':['old','new']}}
    result['required'].append('answer_edits')
    return result


def apply_revision(original,revision):
    if not isinstance(revision,dict) or set(revision)!={'category','model_disclosure','unavailable_reason','sources','answer_edits'}:
        raise ValueError('invalid_revision')
    if (revision['category'] not in ('verified','conversation','unavailable','warning')
        or type(revision['model_disclosure']) is not bool
        or revision['unavailable_reason'] not in ('none','current_information','missing_material','missing_context','ambiguous_request')):
        raise ValueError('invalid_revision')
    sources=revision['sources']
    if not isinstance(sources,list) or any(not isinstance(s,dict) or set(s)!={'url','as_of'}
        or not all(isinstance(s[k],str) for k in ('url','as_of')) for s in sources):
        raise ValueError('invalid_revision')
    edits=revision['answer_edits'];text=original['answer'];spans=[]
    if not isinstance(edits,list) or len(edits)>12:raise ValueError('invalid_revision')
    for edit in edits:
        if not isinstance(edit,dict) or set(edit)!={'old','new'}:raise ValueError('invalid_edit')
        old,new=edit['old'],edit['new']
        if not isinstance(old,str) or not isinstance(new,str) or not old or max(len(old),len(new))>MAX_REPLY_CHARS:
            raise ValueError('invalid_edit')
        if text.count(old)!=1:raise ValueError('ambiguous_edit')
        start=text.index(old);end=start+len(old)
        if any(start<b and end>a for a,b,_ in spans):raise ValueError('overlapping_edits')
        spans.append((start,end,new))
    for start,end,new in sorted(spans,reverse=True):text=text[:start]+new+text[end:]
    if len(text)>MAX_REPLY_CHARS:raise ValueError('revision_too_long')
    if not text.strip() and revision['category']!='warning':raise ValueError('empty_revision')
    result=deepcopy(original)
    result.update({k:v for k,v in revision.items() if k!='answer_edits'})
    result['answer']=text
    if result['category'] in ('warning','unavailable'):result['memories']=[]
    return result
