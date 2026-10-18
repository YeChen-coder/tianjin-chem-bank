"""Small, explicit MathML-to-LaTeX bridge for the structures exercised in this lab."""
import re
from lxml import etree as E
from .math_variant_glyphs import glyph_tables

PARSER = E.XMLParser(resolve_entities=False, no_network=True)
MATHML_NS = 'http://www.w3.org/1998/Math/MathML'
OPERATORS = {'Δ': r'\Delta', '℃': r'\mathrm{℃}', '°': r'\degree', '→': r'\rightarrow', '←': r'\leftarrow',
             '⇌': r'\rightleftharpoons', '⇄': r'\rightleftarrows', '⇀': r'\rightharpoonup', '⇁': r'\rightharpoondown',
             '↑': r'\uparrow', '↓': r'\downarrow', '↔': r'\leftrightarrow', '≥': r'\ge', '≤': r'\le', '≠': r'\ne',
             '⋅': r'\cdot', '·': r'\cdot', '×': r'\times', '−': '-', '∞': r'\infty', '±': r'\pm', '%': r'\%', '_': r'\_'}
OPERATORS.update({'∑':r'\sum','∏':r'\prod','∫':r'\int','∮':r'\oint','⋃':r'\bigcup','⋂':r'\bigcap','⨁':r'\bigoplus'})
VARIANTS = {'normal':'mathrm', 'bold':'mathbf', 'italic':'mathit', 'bold-italic':'boldsymbol',
            'double-struck':'mathbb', 'script':'mathcal', 'fraktur':'mathfrak', 'sans-serif':'mathsf', 'monospace':'mathtt'}
ARITIES = {'msub':2,'msup':2,'msubsup':3,'mfrac':2,'mroot':2,'mover':2,'munder':2,'munderover':3}
LARGE_OPERATORS={'∑','∏','∫','∮','⋃','⋂','⨁'}


def variant(text, name):
    if not name:
        return text
    if name not in VARIANTS:
        raise ValueError('Unsupported mathematical font variant: ' + name)
    return '\\' + VARIANTS[name] + '{' + text + '}'


def text_escape(text):
    replacements = {'\\':r'\backslash ', '{':r'\{', '}':r'\}', '%':r'\%', '_':r'\_', '&':r'\&', '#':r'\#', '$':r'\$'}
    return ''.join(replacements.get(c,c) for c in text)


def escape(text):
    return ''.join(OPERATORS.get(c, r'\{' if c == '{' else r'\}' if c == '}' else c) + (' ' if c in OPERATORS else '') for c in text).strip()


def mathml_to_latex(mathml):
    root = E.fromstring(mathml.encode('utf-8'), PARSER)
    def no_bar(node):
        return E.QName(node).localname=='mfrac' and bool(re.fullmatch(r'0+(?:\.0+)?(?:px|pt|em|ex|%)?',node.get('linethickness','').strip()))
    def render(node):
        name = E.QName(node).localname
        children = list(node)
        if name in ARITIES and len(children) != ARITIES[name]:
            raise ValueError('Incomplete MathML structure: ' + name)
        if name in {'none','mprescripts'}:
            return ''
        if name == 'semantics':
            if not children:
                raise ValueError('MathML semantics has no expression')
            return render(children[0])
        values = [render(child) for child in children]
        if name in {'msub','msup','msubsup','munder','mover','munderover'}:
            base=children[0]
            while E.QName(base).localname in {'mrow','mstyle'} and len(base)==1:base=base[0]
            symbol=''.join(base.itertext()).strip()
            if E.QName(base).localname=='mo' and symbol in LARGE_OPERATORS and not base.get('mathvariant'):
                command=OPERATORS[symbol]+(r'\limits' if name in {'munder','mover','munderover'} else '')
                lower=values[1] if name in {'msub','msubsup','munder','munderover'} else None
                upper=values[-1] if name in {'msup','msubsup','mover','munderover'} else None
                return command+('_{'+lower+'}' if lower is not None else '')+('^{'+upper+'}' if upper is not None else '')
        if name in {'math', 'mrow', 'mstyle'}:
            if name=='mrow' and node.get('data-word-nary-body')=='true' and len(values)==2:
                return values[0]+' {'+values[1]+'}'
            return variant(' '.join(values), node.get('mathvariant'))
        if name in {'mi', 'mn', 'mo', 'mtext'}:
            style=node.get('mathvariant')
            if style and style not in VARIANTS and style in glyph_tables():
                table=glyph_tables()[style];raw=''.join(node.itertext())
                if any(c.isalnum() and c not in table for c in raw):raise ValueError('Unsupported characters in mathematical font variant: '+style)
                return escape(''.join(table.get(c,c) for c in raw))
            text = escape(''.join(node.itertext()))
            if name == 'mtext':
                return r'\text{' + text_escape(''.join(node.itertext())) + '}'
            if node.get('mathvariant'):
                return variant(text, node.get('mathvariant'))
            return text
        if name == 'mspace':
            return r'\,'
        if name == 'msub':
            return '{' + values[0] + '}_{' + values[1] + '}'
        if name == 'msup':
            return '{' + values[0] + '}^{' + values[1] + '}'
        if name == 'msubsup':
            return '{' + values[0] + '}_{' + values[1] + '}^{' + values[2] + '}'
        if name == 'mfrac':
            if no_bar(node):
                return '{'+values[0]+r'\atop '+values[1]+'}'
            return r'\frac{' + values[0] + '}{' + values[1] + '}'
        if name == 'msqrt':
            return r'\sqrt{' + ' '.join(values) + '}'
        if name == 'mroot':
            return r'\sqrt[' + values[1] + ']{' + values[0] + '}'
        if name == 'mover':
            accent = ''.join(children[1].itertext()).strip()
            commands = {'¯':'overline','‾':'overline','̅':'overline','̄':'overline','^':'widehat','ˆ':'widehat','̂':'widehat','~':'widetilde','˜':'widetilde','̃':'widetilde',
                        '˙':'dot','·':'dot','¨':'ddot','→':'overrightarrow','←':'overleftarrow','↔':'overleftrightarrow','⏞':'overbrace'}
            commands.update({'⃗':'overrightarrow','̈':'ddot','̇':'dot','―':'overline'})
            if accent in commands and (node.get('accent') == 'true' or accent in {'¯','‾','⏞'}):
                return '\\' + commands[accent] + '{' + values[0] + '}'
            return r'\overset{' + values[1] + '}{' + values[0] + '}'
        if name == 'munder':
            if node.get('accentunder')=='true' and ''.join(children[1].itertext()).strip() in {'⏟','︸'}:
                return r'\underbrace{'+values[0]+'}'
            if node.get('accentunder') == 'true' and ''.join(children[1].itertext()).strip() == '_':
                return r'\underline{' + values[0] + '}'
            return r'\underset{' + values[1] + '}{' + values[0] + '}'
        if name=='mphantom':return r'\phantom{'+' '.join(values)+'}'
        if name=='mpadded':
            dimensions={key:node.get(key) for key in ('width','height','depth') if node.get(key) is not None}
            if any(not re.fullmatch(r'0+(?:\.0+)?(?:px|pt|em|ex|%)?',value.strip()) for value in dimensions.values()):raise ValueError('Unsupported nonzero formula padding')
            result=' '.join(values)
            if 'height' in dimensions or 'depth' in dimensions:
                option='' if {'height','depth'}<=dimensions.keys() else '[t]' if 'height' in dimensions else '[b]'
                result=r'\smash'+option+'{'+result+'}'
            if 'width' in dimensions:result=r'\mathrlap{'+result+'}'
            return result
        if name == 'munderover':
            return r'\overset{' + values[2] + '}{' + r'\underset{' + values[1] + '}{' + values[0] + '}}'
        if name == 'mfenced':
            if len(children)==1 and node.get('open','(')=='(' and node.get('close',')')==')':
                child=children[0]
                while E.QName(child).localname in {'mrow','mstyle'} and len(child)==1:child=child[0]
                if no_bar(child):return r'\binom{'+render(child[0])+'}{'+render(child[1])+'}'
            separators = ''.join(node.get('separators', ',').split())
            inside = ''
            for i, value in enumerate(values):
                if i:
                    inside += escape(separators[min(i-1,len(separators)-1)]) if separators else ' '
                inside += value
            delimiters = {'':' . ', '{':r'\{', '}':r'\}', '⌊':r'\lfloor ', '⌋':r'\rfloor ', '⌈':r'\lceil ', '⌉':r'\rceil ',
                          '⟨':r'\langle ', '⟩':r'\rangle ', '‖':r'\Vert ', '||':r'\Vert '}
            opening, closing = node.get('open','('), node.get('close',')')
            if any(c not in delimiters and c not in {'(',')','[',']','|','<','>'} for c in (opening,closing)):
                raise ValueError('Unsupported mathematical delimiter')
            return r'\left' + delimiters.get(opening, opening) + ' ' + inside + r'\right' + delimiters.get(closing, closing)
        if name == 'mmultiscripts':
            if not children:
                raise ValueError('Multiscripts has no base')
            marker = next((i for i,n in enumerate(children) if E.QName(n).localname=='mprescripts'),len(children))
            post, pre = values[1:marker], values[marker+1:]
            if len(post)%2 or len(pre)%2:
                raise ValueError('Incomplete prescript or tensor index pair')
            def pair(lower, upper):
                return ('_{'+lower+'}' if lower else '') + ('^{'+upper+'}' if upper else '')
            prefix = ''.join('{}' + pair(pre[i],pre[i+1]) for i in reversed(range(0,len(pre),2)) if pre[i] or pre[i+1])
            result = '{'+values[0]+'}'
            for i in range(0,len(post),2):
                result += ('' if i==0 else '{}') + pair(post[i],post[i+1])
            return prefix + result
        if name == 'menclose':
            result = ' '.join(values)
            notations = node.get('notation','longdiv').split()
            commands = {'box':'boxed','top':'overline','bottom':'underline','updiagonalstrike':'cancel','downdiagonalstrike':'bcancel'}
            for notation in notations:
                if notation not in commands:
                    raise ValueError('Unsupported enclosure notation: '+notation)
                result = '\\'+commands[notation]+'{'+result+'}'
            return result
        if name == 'mtable':
            return r'\begin{matrix}' + r'\\'.join(values) + r'\end{matrix}'
        if name == 'mtr':
            return ' & '.join(values)
        if name == 'mtd':
            return ' '.join(values)
        raise ValueError('Unsupported MathML editor bridge: ' + name)
    return render(root).strip()


def normalize_mathml(mathml, latex=None):
    root = E.fromstring(mathml.encode('utf-8'), PARSER)
    name = E.QName(root).localname
    if E.QName(root).namespace is None and name in {'mrow', 'mi', 'mn', 'mo', 'mfrac', 'msub', 'msup', 'mover', 'munder'}:
        root = E.fromstring(('<math xmlns="' + MATHML_NS + '">' + mathml + '</math>').encode(), PARSER)
    if E.QName(root).namespace != MATHML_NS or E.QName(root).localname != 'math':
        raise ValueError('不是有效的公式结构')
    # MathLive 0.111.1 drops an explicitly empty base from MathML scripts:
    # {}^{x} becomes <msup><mi>x</mi></msup>. Keep the source LaTeX as
    # evidence; never infer a missing base for arbitrary malformed MathML.
    empty_bases = len(re.findall(r'\{\s*\}\s*[_^]', latex or ''))
    incomplete = []
    for node in root.iter():
        name = E.QName(node).localname
        expected = ARITIES.get(name)
        if expected and len(node) != expected:
            if name in {'msub','msup','msubsup'} and len(node) == expected - 1:
                incomplete.append(node)
            else:
                raise ValueError('上下标缺少必要结构，原公式已保留。')
    if incomplete:
        if empty_bases < len(incomplete):
            raise ValueError('上下标缺少底数，无法验证原意，原公式已保留。')
        for node in incomplete:
            node.insert(0, E.Element('{' + MATHML_NS + '}mrow'))
    return root
