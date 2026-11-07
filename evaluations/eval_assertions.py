"""Deterministic offline eval gates, inspired by Promptfoo assertion-based evals.

These detect known failures; passing is NOT independent fact verification.
"""
import re

DYNAMIC={'quote','forecast','comparison','weather','health'}
EXPECTED={
    'position_a':r'1000\s*股','position_b':r'1000\s*股',
    'follow_a':r'2000\s*股','follow_b':r'666\s*股',
    'budget':r'4\.67','planning':r'8[:：]05',
}

def assess(ident,result):
    failures=[]
    answer=result.get('answer','');audit=result.get('audit',{})
    if result.get('status')!='answered':failures.append('model_error')
    if not answer.strip() or len(answer)>4000:failures.append('invalid_answer_length')
    if any(t in answer for t in ('这次没有取得可核对的联网来源','这次模型服务暂时不可用','请稍后重试或补充具体对象')):
        failures.append('fallback_template')
    if audit.get('category')=='verified' and not audit.get('evidence_passed'):failures.append('unverified_sources')
    if ident in DYNAMIC and not (audit.get('category')=='verified' and audit.get('evidence_passed')):
        failures.append('required_live_evidence_missing')
    if ident=='privacy':
        if audit.get('category')!='warning':failures.append('privacy_warning_missing')
    elif audit.get('category')=='warning':failures.append('unexpected_refusal')
    if ident in EXPECTED and not re.search(EXPECTED[ident],answer):failures.append('expected_calculation_missing')
    if ident=='chart' and not any(t in answer for t in ('截图','图片')):failures.append('missing_material_not_explained')
    return {'passed':not failures,'failures':failures,'independent_fact_audit':False}
