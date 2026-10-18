"""Recognize exactly two underlines beneath a temperature annotation."""
import re
from lxml import etree as E

def temperature_relation(mathml):
    root=E.fromstring(mathml.encode(),E.XMLParser(resolve_entities=False,no_network=True))
    def unwrap(node):
        while E.QName(node).localname in {'math','mrow','mstyle'} and len(node)==1:node=node[0]
        return node
    node=unwrap(root)
    for _ in range(2):
        if E.QName(node).localname!='munder' or node.get('accentunder')!='true' or len(node)!=2:return None
        marker=node[1]
        if E.QName(marker).localname!='mo' or ''.join(marker.itertext()).strip() not in {'_','¯','‾','̅','̄','―'}:return None
        node=unwrap(node[0])
    # A third line, scripts, fractions and unrelated labels are not this form.
    if any(E.QName(n).localname not in {'mrow','mstyle','mi','mn','mo','mtext','mspace'} for n in node.iter()):return None
    text=''.join(node.itertext()).strip()
    match=re.fullmatch(r'([+−-]?\d+(?:\.\d+)?)\s*(℃|°\s*C|℉|°\s*F|K)',text)
    if not match:return None
    value=match[1].replace('−','-');unit=match[2].replace(' ','')
    suffix=r'\,\mathrm{K}' if unit=='K' else r'\,\degree\mathrm{'+('F' if unit in {'℉','°F'} else 'C')+'}'
    return r'\overset{'+value+suffix+r'}{=}'
