"""Expand structures that Office XSL otherwise collapses before Word export."""
import copy
from lxml import etree as E
ML='http://www.w3.org/1998/Math/MathML'


def prepare_word_mathml(source):
    root=copy.deepcopy(source)
    def node(name,children=(),text=None,**attributes):
        result=E.Element('{'+ML+'}'+name,attributes)
        result.text=text
        result.extend(children)
        return result
    def script(base,lower,upper):
        low=E.QName(lower).localname!='none'
        high=E.QName(upper).localname!='none'
        if low and high: return node('msubsup',[base,copy.deepcopy(lower),copy.deepcopy(upper)])
        if low: return node('msub',[base,copy.deepcopy(lower)])
        if high: return node('msup',[base,copy.deepcopy(upper)])
        return base
    for element in reversed(list(root.iter())):
        name=E.QName(element).localname
        replacement=None
        if name=='mfenced':
            separators=''.join(element.get('separators',',').split())
            values=[node('mo',text=element.get('open','('),fence='true',form='prefix',stretchy='true')]
            for i,child in enumerate(element):
                if i and separators:
                    values.append(node('mo',text=separators[min(i-1,len(separators)-1)],separator='true'))
                values.append(copy.deepcopy(child))
            values.append(node('mo',text=element.get('close',')'),fence='true',form='postfix',stretchy='true'))
            replacement=node('mrow',values)
        elif name=='mmultiscripts':
            children=list(element)
            marker=next((i for i,n in enumerate(children) if E.QName(n).localname=='mprescripts'),len(children))
            post,pre=children[1:marker],children[marker+1:]
            if len(post)%2 or len(pre)%2 or not children:
                raise ValueError('Incomplete native script pair')
            # Office merges extra postscript pairs into a single lower/upper
            # slot. Keep each pair in its own visual position instead.
            if len(post)>2 or len(pre)>2:
                base=copy.deepcopy(children[0])
                if post: base=script(base,post[0],post[1])
                if pre:
                    base=node('mmultiscripts',[base,node('mprescripts'),copy.deepcopy(pre[0]),copy.deepcopy(pre[1])])
                prefix=[script(node('mrow'),pre[i],pre[i+1]) for i in reversed(range(2,len(pre),2))]
                suffix=[script(node('mrow'),post[i],post[i+1]) for i in range(2,len(post),2)]
                replacement=node('mrow',prefix+[base]+suffix)
        if replacement is not None:
            for key,value in element.attrib.items():
                if key not in {'open','close','separators'}: replacement.set(key,value)
            element.getparent().replace(element,replacement)
    return root
