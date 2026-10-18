"""Read Strict OOXML aliases and final math content without editing source files."""
import copy
from lxml import etree as E
W='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
M='http://schemas.openxmlformats.org/officeDocument/2006/math'
STRICT_ALIASES={
 'http://purl.oclc.org/ooxml/wordprocessingml/main':W,
 'http://purl.oclc.org/ooxml/officeDocument/math':M,
 'http://purl.oclc.org/ooxml/officeDocument/relationships':'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
 'http://purl.oclc.org/ooxml/drawingml/main':'http://schemas.openxmlformats.org/drawingml/2006/main',
 'http://purl.oclc.org/ooxml/drawingml/wordprocessingDrawing':'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing',
 'http://purl.oclc.org/ooxml/drawingml/picture':'http://schemas.openxmlformats.org/drawingml/2006/picture',
}

def transitional_view(root):
    def name(value):
        q=E.QName(value);uri=STRICT_ALIASES.get(q.namespace,q.namespace)
        return '{'+uri+'}'+q.localname if uri else q.localname
    if not any(isinstance(n.tag,str) and (E.QName(n).namespace in STRICT_ALIASES or any(E.QName(a).namespace in STRICT_ALIASES for a in n.attrib)) for n in root.iter()):return root
    def clone(node):
        if not isinstance(node.tag,str):return copy.deepcopy(node)
        out=E.Element(name(node.tag),nsmap={prefix:STRICT_ALIASES.get(uri,uri) for prefix,uri in node.nsmap.items()})
        for key,value in node.attrib.items():out.set(name(key),value)
        out.text,out.tail=node.text,node.tail
        out.extend(clone(child) for child in node)
        return out
    return clone(root)

def final_math_view(root):
    """Accept visible inserted/moved-to math; preserve the original elsewhere."""
    root=copy.deepcopy(root);counts={'inserted':0,'deleted':0,'moved_to':0,'moved_from':0,'empty_decorations_removed':0}
    decorations=[n for n in root.iter() if isinstance(n.tag,str) and E.QName(n).namespace==M and E.QName(n).localname in {'acc','bar','d'} and any(body.xpath('.//w:del|.//w:moveFrom',namespaces={'w':W}) for body in n.findall('{'+M+'}e'))]
    wrappers=[n for n in root.iter() if isinstance(n.tag,str) and E.QName(n).namespace==W and E.QName(n).localname in {'ins','del','moveTo','moveFrom'}]
    for node in wrappers:
        kind=E.QName(node).localname;counts[{'ins':'inserted','del':'deleted','moveTo':'moved_to','moveFrom':'moved_from'}[kind]]+=1
    for node in reversed(wrappers):
        parent=node.getparent()
        if parent is None:continue
        kind=E.QName(node).localname;index=parent.index(node)
        if kind in {'ins','moveTo'}:
            for offset,child in enumerate(list(node)):parent.insert(index+offset,child)
        parent.remove(node)
    if wrappers:
        for run in list(root.findall('.//{'+M+'}r')):
            if not run.findall('{'+M+'}t') and all(E.QName(c).localname.endswith('Pr') for c in run):run.getparent().remove(run)
        for decoration in reversed(decorations):
            bodies=decoration.findall('{'+M+'}e')
            if root not in decoration.iterancestors():continue
            if not any(isinstance(c.tag,str) and not E.QName(c).localname.endswith('Pr') for body in bodies for c in body):
                decoration.getparent().remove(decoration);counts['empty_decorations_removed']+=1
    return root,counts
