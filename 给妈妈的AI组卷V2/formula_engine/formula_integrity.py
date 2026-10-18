"""Check compiler MathML against generated Word content before applying it.

This checks retained characters and explicit large-operator groups. It does
not evaluate mathematics or claim OCR correctness.
"""
from collections import Counter
import unicodedata
from lxml import etree as E
ML='http://www.w3.org/1998/Math/MathML';M='http://schemas.openxmlformats.org/officeDocument/2006/math'
NS={'m':M};PARSER=E.XMLParser(resolve_entities=False,no_network=True)
LARGE={'∑','∏','∫','∮','⋃','⋂','⨁','∬','∭'}
def normal(value):return ''.join(c for c in unicodedata.normalize('NFKD',value).replace('−','-') if not c.isspace() and c not in '\u200b\u2060\u2061\u2062\u2063\u2064')
def kind(node):return E.QName(node).localname
def word_prop(node,group,key,default):
    value=node.find('m:'+group+'/m:'+key,NS)
    return value.get('{'+M+'}val',default) if value is not None else default
def ml_text(node):
    name=kind(node)
    if name in {'annotation','annotation-xml','mspace','none','mprescripts'}:return ''
    if name=='semantics':return ml_text(node[0]) if len(node) else ''
    if name=='mfenced':
        values=[ml_text(c) for c in node];separators=''.join(node.get('separators',',').split())
        body=''.join((separators[min(i-1,len(separators)-1)] if i and separators else '')+value for i,value in enumerate(values))
        return normal(node.get('open','(')+body+node.get('close',')'))
    if name=='mroot' and len(node)==2:return ml_text(node[1])+ml_text(node[0])
    if name in {'mover','munder'} and node.get('accent' if name=='mover' else 'accentunder')=='true':return ml_text(node[0])
    return normal(node.text or '')+''.join(ml_text(c) for c in node if isinstance(c.tag,str))
def word_text(node):
    if node is None or E.QName(node).namespace!=M:return ''
    name=kind(node)
    if name.endswith('Pr'):return ''
    if name=='t':return normal(node.text or '')
    if name=='f' and word_prop(node,'fPr','type','bar')=='lin':
        return word_text(node.find('m:num',NS))+'/'+word_text(node.find('m:den',NS))
    if name=='d':
        values=[word_text(c) for c in node.findall('m:e',NS)]
        return normal(word_prop(node,'dPr','begChr','(')+word_prop(node,'dPr','sepChr','|').join(values)+word_prop(node,'dPr','endChr',')'))
    if name=='nary':return normal(word_prop(node,'naryPr','chr','∫'))+word_text(node.find('m:sub',NS))+word_text(node.find('m:sup',NS))+word_text(node.find('m:e',NS))
    return ''.join(word_text(c) for c in node if isinstance(c.tag,str))
def operator(node):
    name=kind(node);lower=upper=''
    if name in {'msub','msup','msubsup','munder','mover','munderover'}:
        if not len(node):return None
        base=node[0]
        if name in {'msub','msubsup','munder','munderover'}:lower=ml_text(node[1])
        if name in {'msup','msubsup','mover','munderover'}:upper=ml_text(node[-1])
    else:base=node
    while kind(base) in {'mrow','mstyle'} and len(base)==1:base=base[0]
    symbol=normal(base.text or '') if kind(base)=='mo' else ''
    return (symbol,lower,upper) if symbol in LARGE else None

def validate_word_conversion(mathml,omml):
    source=E.fromstring(mathml.encode(),PARSER);target=E.fromstring(omml.encode(),PARSER)
    if Counter(ml_text(source))!=Counter(word_text(target)):
        raise ValueError('公式转换没有完整保留符号，原公式已保留。')
    expected=Counter()
    for row in source.iter():
        if not isinstance(row.tag,str) or kind(row) not in {'math','mrow','mstyle','mtd'}:continue
        children=[c for c in row if isinstance(c.tag,str) and kind(c)!='mspace']
        for i,node in enumerate(children[:-1]):
            bounds=operator(node);body=children[i+1]
            if bounds and kind(body)=='mrow' and ml_text(body):expected[(*bounds,ml_text(body))]+=1
    actual=Counter((normal(word_prop(n,'naryPr','chr','∫')),word_text(n.find('m:sub',NS)),word_text(n.find('m:sup',NS)),word_text(n.find('m:e',NS))) for n in target.findall('.//m:nary',NS))
    if expected-actual:raise ValueError('求和或积分的计算部分未完整保留，原公式已保留。')
