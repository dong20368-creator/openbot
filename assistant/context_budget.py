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


def bounded_history(question,history,budget=24000):
    remaining=budget-estimated_tokens({'本次问题':question})-1000
    if remaining<0: raise ValueError('Question exceeds input context budget')
    def priority(item):
        kind=item.get('kind')
        if kind=='current_person_history' and item.get('continuity_role')=='latest_turn':
            return .5,-item.get('created',0)
        return {'explicit_reference':0,'unresolved_reference':0,
                'current_person_explicit_facts':1}.get(kind,2),-item.get('created',0)
    expanded=[]
    for item in history:
        if item.get('kind')=='current_person_explicit_facts':
            expanded.extend({'kind':item['kind'],'facts':[fact],'created':fact.get('updated',0)} for fact in item['facts'])
        else: expanded.append(item)
    chosen=[]
    for item in sorted(expanded,key=priority):
        if estimated_tokens(item)>2000 and item.get('question'):
            # Retain labelled excerpts rather than silently discarding a long,
            # relevant record. The full original stays in the scoped database.
            item=dict(item); item['excerpt']=True
            for field in ('question','answer'):
                text=item.get(field) or ''
                hits=[text.lower().find(t) for t in search_terms(question)[:32]]
                found=[p for p in hits if p>=0]
                start=max(0,(min(found) if found else 0)-300)
                item[field]=text[start:start+1200]
        cost=estimated_tokens(item)
        if cost<=remaining:
            chosen.append(item); remaining-=cost
    return sorted(chosen,key=lambda x:x.get('created',0))
