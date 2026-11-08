"""Conservative bounded context selection without external embeddings/services."""
import json
import re


def search_terms(text,limit=256):
    terms=[]
    for word in re.findall(r'[\u4e00-\u9fff]+|[A-Za-z0-9_]{2,}',text.lower()):
        if re.fullmatch(r'[\u4e00-\u9fff]+',word):
            terms.extend(word[i:i+2] for i in range(len(word)-1))
        else: terms.append(word)
    return list(dict.fromkeys(terms))[:limit]


def estimated_tokens(value):
    text=json.dumps(value,ensure_ascii=False,separators=(',',':'))
    # Conservative estimate, not a claim about the provider's hidden tokenizer.
    return sum(1 if ord(c)>127 else .3 for c in text)


def excerpt_text(text,question,limit):
    """Retain opening, matching evidence, and final corrections or options."""
    if len(text)<=limit:return text
    edge=max(1,(limit-50)//3)
    found=[text.lower().find(t) for t in search_terms(question)[:32]]
    found=[p for p in found if p>=0]
    center=min(found) if found else len(text)//2
    start=max(0,min(center-edge//2,len(text)-edge))
    spans=sorted([(0,edge),(start,start+edge),(len(text)-edge,len(text))])
    merged=[]
    for a,b in spans:
        if merged and a<=merged[-1][1]:merged[-1]=(merged[-1][0],max(b,merged[-1][1]))
        else:merged.append((a,b))
    return '\n[中间内容省略]\n'.join(text[a:b] for a,b in merged)


def bounded_history(question,history,budget=24000):
    remaining=budget-estimated_tokens({'本次问题':question})-1000
    if remaining<0: raise ValueError('Question exceeds input context budget')
    def priority(item):
        kind=item.get('kind')
        if kind=='current_person_history' and item.get('continuity_role')=='retrieved_turn':
            return .5,item.get('relevance_rank',999)
        if kind=='current_person_history' and item.get('continuity_role')=='latest_turn':
            return .5,-item.get('created',0)
        return {'explicit_reference':0,'unresolved_reference':0,
                'recent_public_bot_reply':.75,
                'current_person_explicit_facts':1}.get(kind,2),-item.get('created',0)
    expanded=[]
    for item in history:
        if item.get('kind')=='current_person_explicit_facts':
            expanded.extend({'kind':item['kind'],'facts':[fact],'created':fact.get('updated',0)} for fact in item['facts'])
        else: expanded.append(item)
    chosen=[];recent=0
    for item in sorted(expanded,key=priority):
        immediate=item.get('kind')=='current_person_history' and item.get('continuity_role')=='latest_turn'
        if immediate:recent+=1
        cap=min(remaining,8000 if immediate and recent<=2 else 4000 if item.get('kind')=='explicit_reference' else 2200)
        if estimated_tokens(item)>cap and (item.get('question') or item.get('answer')):
            metadata={k:v for k,v in item.items() if k not in ('question','answer')}
            available=int(cap-estimated_tokens(metadata)-100)
            fields=[f for f in ('question','answer') if item.get(f)]
            if available<200:continue
            item=dict(item);item['excerpt']=True
            lengths={f:min(len(item[f]),available//len(fields)) for f in fields}
            spare=available-sum(lengths.values())
            for f in fields:
                extra=min(spare,len(item[f])-lengths[f]);lengths[f]+=extra;spare-=extra
            for f in fields:item[f]=excerpt_text(item[f],question,lengths[f])
        cost=estimated_tokens(item)
        if cost<=remaining:
            chosen.append(item); remaining-=cost
    return sorted(chosen,key=lambda x:x.get('created',0))
