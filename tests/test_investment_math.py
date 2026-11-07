import json,queue,time,unittest
from decimal import Decimal
from investment_math import calculate,MAX_EXPRESSIONS
from codex_client import Codex


class InvestmentMathTests(unittest.TestCase):
    def test_units_and_investment_arithmetic(self):
        for expression,expected in [('(1/(1-0.4)-1)*100','66.66666666666666666666666670'),
                                    ('150/100*20','30'),('0.1+0.2','0.3'),
                                    ('(100*10+200*8)/(100+200)','8.666666666666666666666666667'),
                                    ('(1+0.1)**3','1.331'),('100000*0.02/(20-18)','1000')]:
            with self.subTest(expression=expression):self.assertEqual(Decimal(calculate(expression)['result']),Decimal(expected))

    def test_rejects_code_zero_division_and_resource_abuse(self):
        for expression in ('__import__("os")','x+1','1/0','True+1','9**9999','1e9999','1e-999999999','[1]*99','1'*301):
            with self.subTest(expression=expression),self.assertRaises((ValueError,SyntaxError,ArithmeticError)):
                calculate(expression)

    def test_calculator_scope_and_budget(self):
        c=Codex();c.deadline=time.monotonic()+5;c.events=queue.Queue();c.active_thread='allowed';c.math_calls=0
        sent=[];c.send=sent.append
        for thread in ['other']+['allowed']*(MAX_EXPRESSIONS+1):
            c.events.put({'id':len(sent),'method':'item/tool/call','params':{
                'threadId':thread,'namespace':'investment_math','tool':'calculate','arguments':{'expression':'2+2'}}})
            c.receive()
        self.assertIn('error',sent[0]);self.assertEqual(c.math_calls,MAX_EXPRESSIONS)
        self.assertTrue(sent[1]['result']['success']);self.assertFalse(sent[-1]['result']['success'])

    def test_batch_preserves_decimal_accuracy_and_shared_budget(self):
        c=Codex();c.deadline=time.monotonic()+5;c.events=queue.Queue();c.active_thread='allowed';c.math_calls=0
        sent=[];c.send=sent.append
        for values in [['0.1+0.2','100000*0.02/(20-18)'],['1+1']*(MAX_EXPRESSIONS-1),['1+1']*(MAX_EXPRESSIONS-2)]:
            c.events.put({'id':len(sent),'method':'item/tool/call','params':{'threadId':'allowed','namespace':'investment_math','tool':'calculate_many','arguments':{'expressions':values}}})
            c.receive()
        first=json.loads(sent[0]['result']['contentItems'][0]['text'])
        self.assertEqual([Decimal(r['result']) for r in first['results']],[Decimal('0.3'),Decimal('1000')])
        self.assertFalse(sent[1]['result']['success']);self.assertTrue(sent[2]['result']['success'])
        self.assertEqual(c.math_calls,MAX_EXPRESSIONS);self.assertEqual(c.math_tool_rounds,2)

    def test_financial_scenarios_complete_after_twelve_expressions(self):
        c=Codex();c.deadline=time.monotonic()+5;c.events=queue.Queue();c.active_thread='allowed';c.math_calls=12
        sent=[];c.send=sent.append
        c.events.put({'id':1,'method':'item/tool/call','params':{'threadId':'allowed','namespace':'investment_math','tool':'calculate_many','arguments':{'expressions':['5428*0.07']}}})
        c.receive()
        self.assertTrue(sent[0]['result']['success'])
        result=json.loads(sent[0]['result']['contentItems'][0]['text'])
        self.assertEqual(Decimal(result['results'][0]['result']),Decimal('379.96'))

    def test_batch_rejects_wrong_thread_and_unsafe_expression(self):
        c=Codex();c.deadline=time.monotonic()+5;c.events=queue.Queue();c.active_thread='allowed';c.math_calls=0
        sent=[];c.send=sent.append
        for thread in ['other','allowed']:
            c.events.put({'id':len(sent),'method':'item/tool/call','params':{'threadId':thread,'namespace':'investment_math','tool':'calculate_many','arguments':{'expressions':['1+1','__import__("os")']}}})
            c.receive()
        self.assertIn('error',sent[0]);self.assertFalse(sent[1]['result']['success'])


if __name__=='__main__':unittest.main()
