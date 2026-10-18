"""Selected Word text and scripts -> editable candidate, preserving prose."""
import re
from .inline_model import slice_nodes, txt
from .chemistry_parser import characters, Parser, ChemicalSyntaxError
from .math_codec import escape

RELATIONS={'=':'=', '→':r'\rightarrow', '←':r'\leftarrow', '↔':r'\leftrightarrow', '⇌':r'\rightleftharpoons', '⇄':r'\rightleftarrows'}


def chemical_latex(nodes):
    plain=txt(nodes)
    comparison=re.fullmatch(r'\s*pH\s*(>=|<=|>|<|=|≥|≤)\s*(-?\d+(?:\.\d+)?)\s*',plain)
    if comparison and all(c.style=='baseline' for c in characters(nodes)):
        relation={'>=':r'\ge','<=':r'\le','≥':r'\ge','≤':r'\le'}.get(comparison[1],comparison[1])
        return r'\mathrm{pH}'+relation+' '+comparison[2]
    chars=characters(nodes)
    parser=Parser(chars)
    try:
        latex,_=parser.expression()
        parser.space()
        if parser.peek() in RELATIONS:
            symbol=parser.peek()
            parser.pos+=1
            right,_=parser.expression()
            latex+=RELATIONS[symbol]+right
            parser.space()
        if parser.pos!=len(chars):
            raise ChemicalSyntaxError('Unparsed chemical text')
        return latex
    except ChemicalSyntaxError as error:
        raise ValueError('选中文字不是完整的化学式；请缩小选择范围，或使用数学公式模式后手动修正。') from error


def mathematical_latex(nodes):
    chars=characters(nodes)
    formats=[node for node in nodes for _ in (node.get('text','') if node['kind']=='text' else '\t')]
    result=''
    pos=0
    last_unit=None
    prepared=False
    groups=[]
    def token(char,index):
        if char in ' \t\u00a0': return r'\, '
        value=escape(char)
        if value.startswith('\\') and value[-1:].isascii() and value[-1:].isalpha(): value+=' '
        if formats[index].get('bold'):
            value=(r'\mathbf{' if char.isascii() and char.isalnum() else r'\boldsymbol{')+value+'}'
        return value
    while pos<len(chars):
        char=chars[pos]
        if char.style in {'subscript','superscript'}:
            style=char.style
            script=''
            while pos<len(chars) and chars[pos].style==style:
                script+=token(chars[pos].value,pos)
                pos+=1
            if last_unit is None:
                result+='{}';last_unit=len(result)-2;prepared=True
            elif not prepared:
                result=result[:last_unit]+'{'+result[last_unit:]+'}';prepared=True
            result+=('_{' if style=='subscript' else '^{')+script+'}'
        else:
            start=len(result)
            result+=token(char.value,pos)
            if char.value in '([{': groups.append((char.value,start))
            if char.value in ')]}' and groups and {'(':')','[':']','{':'}'}[groups[-1][0]]==char.value:
                last_unit=groups.pop()[1]
            elif char.value not in ' \t\u00a0': last_unit=start
            prepared=False
            pos+=1
    return result.strip()


def convert_text_span(data):
    block=data['block']
    nodes=block.get('inlines',[])
    start,end=data['start'],data['end']
    length=len(txt(nodes))
    if isinstance(start,bool) or isinstance(end,bool) or not isinstance(start,int) or not isinstance(end,int) or not 0<=start<end<=length:
        raise ValueError('无效文字选择范围，请重新选择。')
    source=slice_nodes(nodes,start,end)
    if any(n['kind'] not in {'text','tab'} for n in source):
        raise ValueError('请选择纯文字及其上下标；图片和已有公式请使用各自的编辑功能。')
    if any(n.get('symbol_normalized') is False and n.get('text')=='□' for n in source):
        raise ValueError('选区含有尚未映射的字体字形，请先对照原 Word 补齐；不能将方框当成公式符号。')
    if not txt(source).strip():
        raise ValueError('没有选中公式文字。')
    if data.get('mode')=='chemistry':
        latex=chemical_latex(source)
    elif data.get('mode')=='mathematics':
        latex=mathematical_latex(source)
    else:
        raise ValueError('请选择数学公式或化学式模式。')
    return {'latex':latex,'merge':{'before':slice_nodes(nodes,0,start),'after':slice_nodes(nodes,end,length),'source_parts':source},
            'source_text':txt(source),'note':'只替换所选文字，原有说明文字保留。请对照原文核对公式及上下标。'}
