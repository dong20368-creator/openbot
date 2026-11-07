"""Bounded decimal arithmetic, no eval, names, I/O or executable expressions."""
import ast
from decimal import Decimal,localcontext

MAX_EXPRESSIONS=24


def calculate(expression):
    if not isinstance(expression,str) or not 1<=len(expression)<=300:
        raise ValueError('invalid_expression')
    tree=ast.parse(expression,mode='eval')
    if sum(1 for _ in ast.walk(tree))>100:raise ValueError('expression_too_complex')
    def visit(node,depth=0):
        if depth>20:raise ValueError('expression_too_deep')
        if isinstance(node,ast.Constant) and type(node.value) in (int,float):
            value=Decimal(ast.get_source_segment(expression,node).replace('_',''))
        elif isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.UAdd,ast.USub)):
            value=visit(node.operand,depth+1)
            if isinstance(node.op,ast.USub):value=-value
        elif isinstance(node,ast.BinOp) and isinstance(node.op,(ast.Add,ast.Sub,ast.Mult,ast.Div,ast.Pow)):
            a,b=visit(node.left,depth+1),visit(node.right,depth+1)
            if isinstance(node.op,ast.Add):value=a+b
            elif isinstance(node.op,ast.Sub):value=a-b
            elif isinstance(node.op,ast.Mult):value=a*b
            elif isinstance(node.op,ast.Div):value=a/b
            else:
                if b!=b.to_integral_value() or abs(b)>10:raise ValueError('invalid_exponent')
                value=a**int(b)
        else:raise ValueError('unsupported_expression')
        if not value.is_finite() or abs(value)>Decimal('1e24') or (value and value.adjusted() < -100):
            raise ValueError('result_out_of_range')
        return value
    with localcontext() as context:
        context.prec=28
        result=visit(tree.body)
        return {'expression':expression,'result':format(result,'f'),
                'note':'Arithmetic only; supplied inputs are not verified market data.'}


TOOL={'type':'namespace','name':'investment_math','description':'Bounded decimal arithmetic; does not fetch or verify market facts.',
      'tools':[{'type':'function','name':'calculate','description':'Compute + - * / and integer powers (-10..10). Use consistent units and decimal percentages; no names or functions.',
                'inputSchema':{'type':'object','properties':{'expression':{'type':'string'}},'required':['expression'],'additionalProperties':False}}]}
TOOL['tools'].append({'type':'function','name':'calculate_many',
    'description':'Compute independent scenario formulas together in one call. Same safe decimal arithmetic as calculate. Maximum 24 formulas per question in total.',
    'inputSchema':{'type':'object','properties':{'expressions':{'type':'array','minItems':1,'maxItems':MAX_EXPRESSIONS,'items':{'type':'string','maxLength':300}}},
                   'required':['expressions'],'additionalProperties':False}})
