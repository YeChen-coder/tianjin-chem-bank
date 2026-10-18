"""Rendering corrections shared by the sandbox converter and local editor."""
import copy
from lxml import etree as E

NS = {'m': 'http://schemas.openxmlformats.org/officeDocument/2006/math'}
PARSER = E.XMLParser(resolve_entities=False, no_network=True)

def repair_group_braces(mathml,omml):
    """Use stretching Word group characters for confirmed brace accents."""
    from .formula_integrity import ml_text,word_text
    source=E.fromstring(mathml.encode(),PARSER);root=E.fromstring(omml.encode(),PARSER);records=[]
    for node in source.iter():
        name=E.QName(node).localname
        if name not in {'mover','munder'} or len(node)!=2:continue
        position='top' if name=='mover' else 'bot';symbol=''.join(node[-1].itertext()).strip()
        if symbol not in {'⏞','⏟'}:continue
        records.append({'position':position,'symbol':symbol,'base':ml_text(node[0]),'accent':node.get('accent' if name=='mover' else 'accentunder')=='true','matched':False})
    if not any(r['accent'] for r in records):return omml,0
    replacements=[]
    for node in root.xpath('//*[local-name()="limLow" or local-name()="limUpp" or local-name()="acc" or local-name()="groupChr"]'):
        name=E.QName(node).localname;property_name='groupChrPr' if name=='groupChr' else 'accPr'
        char=node.find('m:'+property_name+'/m:chr',NS);limit=node.find('m:lim',NS)
        symbol=''.join(limit.itertext()).strip() if limit is not None else char.get('{'+NS['m']+'}val','') if char is not None else ''
        if symbol not in {'⏞','⏟'}:continue
        pos=node.find('m:groupChrPr/m:pos',NS)
        position=(pos.get('{'+NS['m']+'}val','bot') if pos is not None else 'bot') if name=='groupChr' else 'bot' if name=='limLow' else 'top'
        base=word_text(node.find('m:e',NS))
        record=next((r for r in records if not r['matched'] and (r['position'],r['symbol'],r['base'])==(position,symbol,base)),None)
        if record is None:continue
        record['matched']=True
        if record['accent'] and name!='groupChr':replacements.append((node,position,symbol))
    for node,position,symbol in reversed(replacements):
        group=E.Element('{'+NS['m']+'}groupChr');props=E.SubElement(group,'{'+NS['m']+'}groupChrPr')
        for key,value in [('chr',symbol),('pos',position),('vertJc','top' if position=='bot' else 'bot')]:E.SubElement(props,'{'+NS['m']+'}'+key).set('{'+NS['m']+'}val',value)
        group.append(copy.deepcopy(node.find('m:e',NS)));node.getparent().replace(node,group)
    if any(r['accent'] and not r['matched'] for r in records):raise ValueError('Native group brace structure changed')
    return E.tostring(root,encoding='unicode'),len(replacements)


def repair_empty_script_bases(omml):
    """A zero-width base prevents Word printing a placeholder-width gap."""
    root=E.fromstring(omml.encode(),PARSER)
    for script in root.xpath('//*[local-name()="sSub" or local-name()="sSup" or local-name()="sSubSup"]'):
        base=script.find('m:e',NS)
        if base is not None and not len(base):
            run=E.SubElement(base,'{'+NS['m']+'}r')
            text=E.SubElement(run,'{'+NS['m']+'}t')
            text.text='\u200b'
    return E.tostring(root,encoding='unicode')


def repair_accent_bars(mathml, omml):
    source = E.fromstring(mathml.encode(), PARSER)
    markers={'top':{'¯','‾','̅','̄','―'},'bot':{'_','¯','‾','̅','̄','―'}}
    expected={'top':0,'bot':0}
    def base_text(node):
        if node is None:raise ValueError('Missing native accent base')
        name=E.QName(node).localname
        if name.endswith('Pr'):return ''
        if name in {'mover','munder','munderover'}:return base_text(node[0])
        if name in {'bar','acc','limLow','limUpp'}:return base_text(node.find('m:e',NS))
        if name=='nary':
            char=node.find('m:naryPr/m:chr',NS)
            return (char.get('{'+NS['m']+'}val','∫') if char is not None else '∫')+base_text(node.find('m:e',NS))
        if name=='mroot':return base_text(node[1])+base_text(node[0])
        return ''.join((node.text or '').split()).replace('\u200b','')+''.join(base_text(child) for child in node)
    records=[]
    for node in source.iter():
        name=E.QName(node).localname
        if name not in {'mover','munder'} or len(node)!=2:
            continue
        position='top' if name=='mover' else 'bot'
        accent=node.get('accent' if position=='top' else 'accentunder')=='true' or node[-1].get('accent')=='true'
        if ''.join(node[-1].itertext()).strip() in markers[position]:
            records.append({'position':position,'base':base_text(node[0]),'bar':accent,'matched':False})
            if accent:expected[position]+=1
    if not any(expected.values()):
        return omml, 0
    root = E.fromstring(omml.encode(), PARSER)
    fixed = 0
    replacements=[]
    candidates=root.xpath('//*[local-name()="limLow" or local-name()="limUpp" or local-name()="acc" or local-name()="bar"]')
    for node in candidates:
        name=E.QName(node).localname
        pos=node.find('m:barPr/m:pos',NS)
        position=(pos.get('{'+NS['m']+'}val','bot') if pos is not None else 'bot') if name=='bar' else 'bot' if name=='limLow' else 'top'
        limit=node.find('m:lim',NS)
        char=node.find('m:accPr/m:chr',NS)
        marker=''.join(limit.itertext()).strip() if limit is not None else char.get('{'+NS['m']+'}val','') if char is not None else ''
        if name!='bar' and marker not in markers[position]:continue
        record=next((r for r in records if not r['matched'] and r['position']==position and r['base']==base_text(node.find('m:e',NS))),None)
        if record is None:continue
        record['matched']=True
        if not record['bar']:
            if name=='bar':raise ValueError('Literal annotation changed into a native bar')
            continue
        if name=='bar':continue
        replacements.append((node,position))
    for node,position in reversed(replacements):
        bar = E.Element('{' + NS['m'] + '}bar')
        props = E.SubElement(bar, '{' + NS['m'] + '}barPr')
        property_node = E.SubElement(props, '{' + NS['m'] + '}pos')
        property_node.set('{' + NS['m'] + '}val', position)
        bar.append(copy.deepcopy(node.find('m:e', NS)))
        node.getparent().replace(node, bar)
        fixed += 1
    if any(r['bar'] and not r['matched'] for r in records):raise ValueError('Native bar structure count changed')
    for array in list(root.findall('.//m:eqArr', NS)):
        expressions = array.findall('m:e', NS)
        nonempty = [e for e in expressions if len(e)]
        if len(nonempty) == 1:
            parent = array.getparent()
            index = parent.index(array)
            parent.remove(array)
            for i, child in enumerate(list(nonempty[0])):
                parent.insert(index + i, copy.deepcopy(child))
    return E.tostring(root, encoding='unicode'), fixed
