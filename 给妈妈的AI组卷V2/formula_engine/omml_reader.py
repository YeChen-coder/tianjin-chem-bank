"""Direct native Word math reader, preserving symbols and font variants.

Unknown structures fail explicitly so the retained Office conversion/original
can be used. No unsupported structure is flattened silently to plain text.
"""
import re
from lxml import etree as E
M='http://schemas.openxmlformats.org/officeDocument/2006/math'
W='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
ML='http://www.w3.org/1998/Math/MathML'
NS={'m':M,'w':W}
PARSER=E.XMLParser(resolve_entities=False,no_network=True)


def element(name,children=(),text=None,**attributes):
    node=E.Element('{'+ML+'}'+name,attributes)
    node.text=text
    node.extend(children)
    return node


def omml_to_mathml(source):
    root=E.fromstring(source.encode(),PARSER)
    def prop(node,path,default=None):
        item=node.find(path,NS)
        return item.get('{'+M+'}val',default) if item is not None else default
    def slot(node,name):
        value=node.find('m:'+name,NS)
        if value is None:
            raise ValueError('Missing native formula slot: '+name)
        return row(value)
    def contents(node):
        for child in node:
            if not isinstance(child.tag,str):continue
            if E.QName(child).namespace!=M and ''.join(child.itertext()).strip():
                raise ValueError('Unsupported mixed native formula text')
        return [read(c) for c in node if isinstance(c.tag,str) and E.QName(c).namespace==M and not E.QName(c).localname.endswith('Pr')]
    def row(node):
        values=contents(node)
        return values[0] if len(values)==1 else element('mrow',values)
    def read(node):
        name=E.QName(node).localname
        if name in {'oMath','oMathPara','e','sub','sup','num','den','deg','lim','fName'}:
            return row(node)
        if name=='r':
            text=''.join(n.text or '' for n in node.findall('m:t',NS))
            if text=='\u200b':
                return element('mrow')
            if node.find('m:rPr/m:nor',NS) is not None:
                return element('mtext',text=text)
            style=prop(node,'m:rPr/m:sty')
            font={'p':'normal','b':'bold','bi':'bold-italic','i':'italic'}.get(style)
            script=prop(node,'m:rPr/m:scr')
            font={'double-struck':'double-struck','script':'script','fraktur':'fraktur','sans-serif':'sans-serif','monospace':'monospace'}.get(script,font)
            if script in {'script','fraktur'} and style in {'b','bi'}:font='bold-'+script
            if script=='sans-serif':font={'b':'bold-sans-serif','i':'sans-serif-italic','bi':'sans-serif-bold-italic','p':'sans-serif'}.get(style,'sans-serif-italic')
            if not font and node.find('w:rPr/w:b',NS) is not None:
                font='bold'
            values=[]
            for token in re.findall(r'[0-9]+(?:\.[0-9]+)?|[A-Za-z]+|\s+|.',text):
                parts=list(token) if token.isascii() and token.isalpha() and len(token)>1 and font!='normal' else [token]
                for part in parts:
                    kind='mtext' if part.isspace() else 'mn' if re.fullmatch(r'[0-9]+(?:\.[0-9]+)?',part) else 'mi' if part.isalpha() else 'mo'
                    values.append(element(kind,text=part,**({'mathvariant':font} if font and kind in {'mi','mn'} else {})))
            return values[0] if len(values)==1 else element('mrow',values)
        scripts={'sSub':('msub',['e','sub']),'sSup':('msup',['e','sup']),'sSubSup':('msubsup',['e','sub','sup'])}
        if name in scripts:
            tag,slots=scripts[name]
            return element(tag,[slot(node,key) for key in slots])
        if name=='sPre':
            return element('mmultiscripts',[slot(node,'e'),element('mprescripts'),slot(node,'sub'),slot(node,'sup')])
        if name=='f':
            kind=prop(node,'m:fPr/m:type','bar')
            if kind not in {'bar','noBar','lin','skw'}:
                raise ValueError('Unsupported native fraction layout: '+kind)
            attributes={'linethickness':'0'} if kind=='noBar' else {}
            if kind in {'lin','skw'}:attributes['data-word-fraction-layout']=kind
            if kind=='skw':attributes['bevelled']='true'
            return element('mfrac',[slot(node,'num'),slot(node,'den')],**attributes)
        if name=='rad':
            degree=node.find('m:deg',NS)
            hidden=prop(node,'m:radPr/m:degHide','0')
            if hidden in {'1','true','on'} or degree is None or not ''.join(degree.itertext()).strip():
                return element('msqrt',[slot(node,'e')])
            return element('mroot',[slot(node,'e'),slot(node,'deg')])
        if name=='d':
            return element('mfenced',[row(c) for c in node.findall('m:e',NS)],open=prop(node,'m:dPr/m:begChr','('),close=prop(node,'m:dPr/m:endChr',')'),separators=prop(node,'m:dPr/m:sepChr','|'))
        if name=='acc':
            return element('mover',[slot(node,'e'),element('mo',text=prop(node,'m:accPr/m:chr','ˆ'))],accent='true')
        if name=='bar':
            below=prop(node,'m:barPr/m:pos','bot')=='bot'
            return element('munder' if below else 'mover',[slot(node,'e'),element('mo',text='_' if below else '¯')],**({'accentunder':'true'} if below else {'accent':'true'}))
        if name=='groupChr':
            position=prop(node,'m:groupChrPr/m:pos','bot');symbol=prop(node,'m:groupChrPr/m:chr','⏟')
            symbol={'︷':'⏞','︸':'⏟','︵':'⏜','︶':'⏝'}.get(symbol,symbol)
            result=element('munder' if position=='bot' else 'mover',[slot(node,'e'),element('mo',text=symbol,stretchy='true')],**({'accentunder':'true'} if position=='bot' else {'accent':'true'}))
            baseline=node.find('m:groupChrPr/m:vertJc',NS)
            result.set('data-word-group-character','true');result.set('data-word-group-baseline','top' if baseline is None else baseline.get('{'+M+'}val','bot'))
            return result
        if name=='phant':
            def enabled(key,default=False):
                item=node.find('m:phantPr/m:'+key,NS)
                return default if item is None else item.get('{'+M+'}val','1') not in {'0','false','off'}
            contents=slot(node,'e');show=enabled('show',True)
            result=contents if show else element('mphantom',[contents])
            dimensions={dimension:'0' for key,dimension in [('zeroWid','width'),('zeroAsc','height'),('zeroDesc','depth')] if enabled(key)}
            if dimensions:result=element('mpadded',[result],**dimensions)
            if enabled('transp'):result.set('data-word-transparent-spacing','true')
            return result
        if name in {'limLow','limUpp'}:
            below=name=='limLow'
            return element('munder' if below else 'mover',[slot(node,'e'),slot(node,'lim')],**({'accentunder':'false'} if below else {'accent':'false'}))
        if name=='nary':
            operator=element('mo',text=prop(node,'m:naryPr/m:chr','∫'),largeop='true')
            lower=node.find('m:sub',NS)
            upper=node.find('m:sup',NS)
            low=lower is not None and prop(node,'m:naryPr/m:subHide','0') not in {'1','true','on'} and ''.join(lower.itertext()).strip()
            high=upper is not None and prop(node,'m:naryPr/m:supHide','0') not in {'1','true','on'} and ''.join(upper.itertext()).strip()
            underover=prop(node,'m:naryPr/m:limLoc','subSup')=='undOvr'
            if low and high:
                operator=element('munderover' if underover else 'msubsup',[operator,row(lower),row(upper)])
            elif low:
                operator=element('munder' if underover else 'msub',[operator,row(lower)])
            elif high:
                operator=element('mover' if underover else 'msup',[operator,row(upper)])
            return element('mrow',[operator,slot(node,'e')],**{'data-word-nary-body':'true'})
        if name=='m':
            return element('mtable',[element('mtr',[element('mtd',[row(c)]) for c in r.findall('m:e',NS)]) for r in node.findall('m:mr',NS)])
        if name=='eqArr':
            return element('mtable',[element('mtr',[element('mtd',[row(c)])]) for c in node.findall('m:e',NS)])
        if name=='func':
            return element('mrow',[slot(node,'fName'),element('mo',text='\u2061'),slot(node,'e')])
        if name=='borderBox':
            if any(prop(node,'m:borderBoxPr/m:'+key,'0') in {'1','true','on'} for key in ['hideTop','hideBot','hideLeft','hideRight','strikeH','strikeV','strikeTLBR','strikeBLTR']):
                raise ValueError('Unsupported partial native border box')
            return element('menclose',[slot(node,'e')],notation='box')
        raise ValueError('Unsupported native Word math structure: '+name)
    result=E.Element('{'+ML+'}math',nsmap={None:ML})
    result.append(read(root))
    E.cleanup_namespaces(result)
    return E.tostring(result,encoding='unicode')
