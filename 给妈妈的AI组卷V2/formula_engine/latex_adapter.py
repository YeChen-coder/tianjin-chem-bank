"""Pinned latex2mathml adapter: preserve upright fonts and explicit accents.

The editor's MathML serializer can omit SVG-backed bases. Compile its complete
LaTeX instead. These local process patches apply only to the pinned converter,
never to a globally installed package or the external source checkout.
"""
import re
from xml.etree import ElementTree as ET
from html import unescape
from lxml import etree as E
from latex2mathml import converter, commands, walker
from .math_variant_glyphs import glyph_tables

_installed = False


def convert_latex(latex):
    global _installed
    if not _installed:
        # These changes live in this converter process, inside the sandbox.
        # The tokenizer keeps \text{} content in a single literal token.
        tokenize=walker.tokenize
        def layout_tokens(data):
            values=list(tokenize(data));i=0
            while i<len(values):
                if values[i]==r'\smash' and values[i+1:i+4] in [['[','t',']'],['[','b',']']]:
                    yield r'\mathlabsmash'+values[i+2];i+=4
                else:yield values[i];i+=1
        walker.tokenize=layout_tokens
        padding={r'\smash':{'height':'0','depth':'0'},r'\mathlabsmasht':{'height':'0'},r'\mathlabsmashb':{'depth':'0'},r'\mathrlap':{'width':'0'},r'\rlap':{'width':'0'}}
        commands.COMMANDS_WITH_ONE_PARAMETER=tuple(commands.COMMANDS_WITH_ONE_PARAMETER)+(r'\smash',r'\rlap')
        for command,attributes in padding.items():commands.CONVERSION_MAP[command]=('mpadded',attributes)
        normal = dict(commands.LOCAL_FONTS[r'\mathrm'])
        normal.update({key:'normal' for key in ['mi','mn','mo','mtext','fence']})
        commands.LOCAL_FONTS[r'\mathrm'] = normal
        original = converter._convert_command
        above = {r'\overrightarrow',r'\overleftarrow',r'\overleftrightarrow',r'\overline',r'\bar',r'\widehat',r'\hat',r'\widetilde',r'\tilde',r'\vec',r'\dot',r'\ddot',r'\overbrace'}
        below = {r'\underline',r'\underbrace'}
        def preserve_accent(node,parent,font=None):
            index=len(parent)
            original(node,parent,font)
            if node.text is not None and node.token in {commands.TEXT,commands.TEXTBF,commands.TEXTIT,commands.TEXTRM,commands.TEXTSF,commands.TEXTTT}:
                text=re.sub(r'\\([&#%_${}])',lambda match:match[1],node.text)
                text=re.sub(r'\\backslash(?:\s|$)',lambda match:'\\',text)
                for part in list(parent)[index:]:
                    for literal in part.iter('mtext'):literal.text=text.replace(' ','\u00a0')
            if node.token in above|below:
                for element in list(parent)[index:]:
                    if element.tag in {'mover','munder'}:
                        element.set('accent' if element.tag=='mover' else 'accentunder','true')
            # Fence tokens are numeric references before serialization.
            parts=list(parent)[index:]
            if len(parts)==3 and parts[0].tag=='mo' and unescape(parts[0].text or '')=='(' and parts[1].tag=='mfrac' and parts[1].get('linethickness')=='0' and parts[2].tag=='mo' and unescape(parts[2].text or '')==')':
                    group=parent.makeelement('mrow',{})
                    for element in parts:parent.remove(element);group.append(element)
                    for element,form in [(parts[0],'prefix'),(parts[2],'postfix')]:
                        element.set('fence','true');element.set('stretchy','true');element.set('form',form)
                        element.attrib.pop('minsize',None);element.attrib.pop('maxsize',None)
                    parent.insert(index,group)
        converter._convert_command=preserve_accent
        _installed=True
    # The upstream tokenizer folds \mathrm{C} into bare C and loses the
    # upright declaration. Whitespace avoids that one-character shortcut.
    latex=re.sub(r'\\mathrm\{([A-Za-z])\}',lambda m:r'\mathrm{ '+m[1]+' }',latex)
    # Upstream's serializer unescapes every '&amp;' and '&lt;', including
    # literal prose. Serialize its tree safely instead; only generated
    # numeric symbol references outside mtext are decoded.
    tree=converter.convert_to_element(latex)
    for token in tree.iter():
        if token.tag!='mtext' and token.text:
            token.text=re.sub(r'&#(?:x([0-9A-Fa-f]+)|([0-9]+));',lambda match:chr(int(match[1],16) if match[1] else int(match[2])),token.text)
    result=E.fromstring(ET.tostring(tree,encoding='utf-8'))
    # KaTeX/MathLive's degree command is an actual degree sign, not the
    # circle operator produced by \circ. Keep literal mtext untouched.
    for token in result.xpath('//*[local-name()="mi" and text()="\\degree"]'):
        token.tag='{http://www.w3.org/1998/Math/MathML}mo';token.text='°'
    # The pinned parser treats \nolimits as an identifier. Recover only its
    # unambiguous operator-adjacent tree; text inside \text{} stays literal.
    operators={'∑','∏','∫','∮','⋃','⋂','⨁','∬','∭'}
    for token in list(result.xpath('//*[local-name()="mi" and text()="\\nolimits"]')):
        parent=token.getparent();name=E.QName(parent).localname
        container=parent.getparent() if name in {'msub','msup','msubsup'} and len(parent) and parent[0] is token else parent
        target=parent if container is not parent else token;previous=target.getprevious()
        if previous is None or E.QName(previous).localname!='mo' or (previous.text or '') not in operators:continue
        if target is token:container.remove(token)
        else:
            container.remove(previous);parent.remove(token);parent.insert(0,previous)
    for token in result.xpath('//*[local-name()="mi" or local-name()="mn" or local-name()="mo"]'):
        unknown=re.search(r'\\[A-Za-z]+',token.text or '')
        if unknown:raise ValueError('Unsupported formula command: '+unknown[0])
    encoded={glyph:(plain,style) for style,table in glyph_tables().items() for plain,glyph in table.items()}
    for token in result.xpath('//*[local-name()="mi" or local-name()="mn"]'):
        if token.text not in encoded:continue
        plain,style=encoded[token.text];declared=token.get('mathvariant','')
        if 'bold' in declared:
            style={'script':'bold-script','fraktur':'bold-fraktur','sans-serif':'bold-sans-serif','sans-serif-italic':'sans-serif-bold-italic','italic':'bold-italic'}.get(style,style)
        token.text=plain;token.set('mathvariant',style)
    return E.tostring(result,encoding='unicode')
