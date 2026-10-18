"""Check the reported examples reach native Word math with unchanged data."""
import json
from pathlib import Path
import sys
from zipfile import ZipFile
from lxml import etree
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from question_content.http_api import ContentAPI,AssetResolver
from word_export_v2 import export_paper
api=ContentAPI(ROOT/'.work/session-4/content-v2',ROOT)
ids=['question-5eeaab0149c0ac39d5383289','question-8507665cd8a94b5686f16bf8']
questions=[api.store.get_question(q)['question'] for q in ids]
bundle=api.bank.selection({'ids':[api.workflow._legacy_id(q) for q in ids],'expected_revisions':{q['id']:q['revision'] for q in questions},'title':'化学式括号与下标检查'})
out=ROOT/'.work/session-4/chemistry-repair/化学式修复检查.docx'
result=export_paper(bundle,out,{'auto_number':True},AssetResolver(api.store))
with ZipFile(out) as archive:root=etree.fromstring(archive.read('word/document.xml'))
ns={'m':'http://schemas.openxmlformats.org/officeDocument/2006/math'}
native=[''.join(n.xpath('.//m:t/text()',namespaces=ns)) for n in root.xpath('//m:oMath',namespaces=ns)]
assert 'Cu(Fe)' in native,native
assert any('X' in s and 'NO' in s and '3' in s and '2' in s for s in native),native
assert any('Z' in s and 'NO' in s and '3' in s and '2' in s for s in native),native
receipt={'output':str(out),'native_math_text':native,'report':result['report']}
(out.parent/'export-check.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
print('Native Word formula checks passed:',len(native))
