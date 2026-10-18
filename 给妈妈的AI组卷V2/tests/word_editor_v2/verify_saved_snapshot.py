"""Separate process verifies persistent API result and downloaded DOCX structure."""
from pathlib import Path
from zipfile import ZipFile
import json, sys
from lxml import etree
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from question_content.storage import ContentStore, nodes
workspace=Path(sys.argv[1]);report=json.loads((workspace/'browser-integration-report.json').read_text(encoding='utf-8'))
after=json.loads((workspace/'after.json').read_text(encoding='utf-8'))
store=ContentStore(workspace)
assert store.snapshot(after['paper']['id'])==after
with ZipFile(workspace/'browser-edited-paper.docx') as archive:
    doc=etree.fromstring(archive.read('word/document.xml'))
    ns={'m':'http://schemas.openxmlformats.org/officeDocument/2006/math','w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    formulas=len(doc.xpath('//m:oMath',namespaces=ns));tables=len(doc.xpath('//w:tbl',namespaces=ns));pictures=len(doc.xpath('//w:drawing',namespaces=ns))
    expected_formulas=sum(n.get('type')=='formula' and n.get('latex') is not None for n in nodes(after['questions']))
    expected_tables=sum(n.get('type')=='table' for n in nodes(after['questions']))
    assert formulas==expected_formulas,(formulas,expected_formulas)
    assert tables==expected_tables,(tables,expected_tables)
    assert pictures==sum(n.get('type')=='image' for n in nodes(after['questions']))
report['checks'].extend(['Separate process reopens persisted ContentStore and matches saved bundle','Downloaded DOCX formula/table/picture counts match saved snapshot'])
report['docxCounts']={'formulas':formulas,'tables':tables,'pictures':pictures}
(workspace/'browser-integration-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
