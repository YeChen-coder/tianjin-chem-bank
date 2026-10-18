"""Restore underlined answer spaces from the retained OOXML, by stable node ID."""
import hashlib
from pathlib import Path
from zipfile import ZipFile,is_zipfile
from lxml import etree
from .storage import nodes


def restore_source_blanks(bundle,original):
    path=Path(original)
    has_document=False
    if is_zipfile(path):
        with ZipFile(path) as archive:has_document='word/document.xml' in archive.namelist()
    if not has_document:path=path.parent/'converted.docx'
    if not is_zipfile(path):return 0
    ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'};w='{'+ns['w']+'}'
    with ZipFile(path) as z:root=etree.fromstring(z.read('word/document.xml'),etree.XMLParser(resolve_entities=False,no_network=True))
    sha=bundle['paper']['source_sha256'];blanks={}
    for t in root.findall('.//w:r/w:t',ns):
        text=t.text or '';run=t.getparent();underline=run.find('w:rPr/w:u',ns)
        if not text or text.strip() or underline is None or underline.get(w+'val','single') in {'none','0','false'}:continue
        size=run.find('w:rPr/w:sz',ns);pt=float(size.get(w+'val','24'))/2 if size is not None else 12
        locator='word/document.xml:'+root.getroottree().getpath(t)
        nid='text-'+hashlib.sha256((sha+':'+locator).encode()).hexdigest()[:24]
        blanks[nid]=(text,max(5,min(100,len(text)*pt*25.4/72*.5)))
    changed=0
    for n in nodes(bundle):
        source=blanks.get(n.get('id'))
        if source and n.get('type')=='text' and n.get('text')==source[0]:
            nid=n['id'];n.clear();n.update(id=nid,type='blank',width_mm=round(source[1],2));changed+=1
    return changed
