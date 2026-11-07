"""Public/synthetic lifestyle dry runs; no queue, group sends or stored member history."""
import concurrent.futures
import json
import time
from pathlib import Path
from investment_eval import EvaluationModel

CASES = [
    ('comfort', '今天上班累死了，安慰我一下，别讲大道理。', []),
    ('recipe', '家里只有鸡蛋、西红柿、面条和盐油，两个人，20分钟内晚饭怎么做？给具体用量和顺序。', []),
    ('laundry', '白色纯棉T恤刚沾了咖啡，家里有普通洗衣液，怎么处理？', []),
    ('budget', '我们三个人吃饭一共286元，我先付了。另外两人各给我100元，我应该各退多少？', []),
    ('translate', '把“麻烦到了给我发个消息，我下楼接你”翻译成自然的英语，朋友之间说的。', []),
    ('planning', '明早9点出门，洗漱20分钟、早餐15分钟、收拾10分钟，还想留10分钟余量，最晚几点起？', []),
    ('followup', '不吃鸡蛋了，有豆腐，还是两个人，怎么改？', [
        {'kind':'current_person_history','continuity_role':'latest_turn','question':'西红柿鸡蛋面，两个人，20分钟内。','answer':'用200克面条、2个鸡蛋、2个西红柿做汤面。','created':time.time()-60}]),
    ('weather', '上海今天晚上适合外出散步吗，要不要带伞？请查当前预报，说明是哪天。', []),
    ('shopping', '现在在中国大陆买iPhone 17还是18标准版更划算？按官方当前价格和主要区别比较，不猜参数。', []),
    ('health', '成年人喉咙痛一天，没有呼吸困难，能不能自己吃阿莫西林？先给结论，再说怎么处理。', []),
    ('food_safety', '米饭昨晚煮好后在室温下放了一整夜，早上炒热还能吃吗？', []),
    ('ambiguity', '明天去机场来得及吗？', []),
]

def run(case):
    ident, question, history = case
    model = EvaluationModel(timeout=150)
    started = time.monotonic()
    result = {'id':ident,'question':question,'sent_to_group':False}
    try:
        result.update(answer=model.answer(question,history),audit=model.last_audit,status='answered')
    except Exception as exc:
        result.update(status='error',error_type=type(exc).__name__)
    result['elapsed_seconds'] = round(time.monotonic()-started,2)
    result['web_observations'] = model.web_observations
    result['draft_sources'] = model.draft_sources
    target = Path(__file__).parent/'runtime/lifestyle-eval'
    target.mkdir(exist_ok=True)
    (target/(ident+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return {k:v for k,v in result.items() if k not in ('question','answer','web_observations','draft_sources')}

if __name__ == '__main__':
    import sys
    cases = [case for case in CASES if len(sys.argv)==1 or case[0] in sys.argv[1:]]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run,case) for case in cases]
        for future in concurrent.futures.as_completed(futures):
            print(json.dumps(future.result(),ensure_ascii=True),flush=True)
