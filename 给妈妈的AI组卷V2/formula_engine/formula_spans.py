"""Recover chemical text around a condition image without consuming prose.

Applies to any paragraph with the same token grammar, independent of document,
question id, asset name, or coordinates. The merge is presented for confirmation.
"""
import re
from lxml import etree as E
from .inline_model import txt, slice_nodes
from .chemistry_parser import parse_prefix, parse_suffix
from .math_codec import mathml_to_latex

RELATIONS = {'=', '→', '←', '↔', '⇌', '⇄', '⇀', '⇁'}
LATEX_RELATIONS = {'=', r'\rightarrow', r'\leftarrow', r'\leftrightarrow', r'\rightleftharpoons', r'\rightleftarrows', r'\rightharpoonup', r'\rightharpoondown'}


def canonical_relation(latex, mathml=None):
    """Only a relation with optional annotations, not an entire OCR equation."""
    if mathml:
        try:
            root = E.fromstring(mathml.encode(), E.XMLParser(resolve_entities=False, no_network=True))
            def base(node):
                name = E.QName(node).localname
                children = [n for n in node if E.QName(n).localname != 'mspace']
                if name in {'math', 'mrow', 'mstyle'} and len(children) == 1:
                    return base(children[0])
                if name in {'mover', 'munder', 'munderover'} and len(children) in {2, 3}:
                    return base(children[0])
                return name == 'mo' and ''.join(node.itertext()).strip() in RELATIONS
            if base(root):
                return mathml_to_latex(mathml)
            from .legacy_conditions import temperature_relation
            return temperature_relation(mathml)
        except (ValueError, E.XMLSyntaxError, IndexError):
            return None
    # Geometric recognition produces a known relation without OCR MathML.
    if latex.strip() in LATEX_RELATIONS:
        return latex.strip()
    if re.fullmatch(r'\\overset\{\\Delta\}\{=\}', latex.strip()):
        return latex.strip()
    return None


def suggest_condition_merge(block, node_id, relation_latex, relation_mathml=None):
    relation_latex = canonical_relation(relation_latex, relation_mathml)
    if relation_latex is None:
        return None
    nodes = block.get('inlines', [])
    index = next((i for i, n in enumerate(nodes) if n.get('id') == node_id), None)
    if index is None:
        return None
    left_nodes, right_nodes = nodes[:index], nodes[index+1:]
    left, right = txt(left_nodes), txt(right_nodes)
    a, b = parse_suffix(left_nodes), parse_prefix(right_nodes)
    if not a or not b:
        return None
    return {'before': slice_nodes(left_nodes, 0, a['start']), 'after': slice_nodes(right_nodes, b['end'], len(right)),
            'latex': a['latex'] + relation_latex + b['latex'],
            'source_parts': [*slice_nodes(left_nodes, a['start'], len(left)), nodes[index], *slice_nodes(right_nodes, 0, b['end'])],
            'note': '同时恢复关系符号两侧的化学式，保留说明文字、已有上下标和电荷；请核对公式边界。'}
