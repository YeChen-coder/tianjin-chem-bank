"""Curate two real source paragraphs for export QA; no splitting/import pipeline."""
import hashlib
import json
from pathlib import Path
import sys
from zipfile import ZipFile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from lxml import etree as E
from formula_engine.engine import native_formula
from question_content import validate_bundle
from question_content.storage import ContentStore
from question_content.http_api import AssetResolver
from word_export_v2 import export_paper

ROOT=Path(__file__).resolve().parents[1]
W='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
M='http://schemas.openxmlformats.org/officeDocument/2006/math'


def main():
    sha='00fd3de9e07679fed7e5f20659cd43234f9c986b12d6ba3a5b8000c240e145c4'
    mirror=ROOT.parent/'chem-papers/mirror'
    source=next((p for p in sorted(mirror.rglob('*.docx')) if hashlib.sha256(p.read_bytes()).hexdigest()==sha),None)
    if source is None:raise RuntimeError('找不到已确认摘要的原件，拒绝用实验稿冒充 mirror 来源')
    with ZipFile(source) as z:doc=E.fromstring(z.read('word/document.xml'),E.XMLParser(resolve_entities=False,no_network=True))
    dest=ROOT/'fixtures/word_latex_v1/real';dest.mkdir(parents=True,exist_ok=True)
    blocks=[];sources={};locators=[];seq=0
    def id_(kind):
        nonlocal seq
        seq+=1;return 'real-excerpt-'+kind+'-'+str(seq)
    for index,p in enumerate(doc.findall('.//{'+W+'}p')):
        if not p.findall('.//{'+M+'}oMath') or p.findall('.//{'+W+'}drawing') or p.findall('.//{'+W+'}sym') or p.findall('.//{'+W+'}object'):continue
        if any(child.tag not in {'{'+W+'}pPr','{'+W+'}r','{'+W+'}bookmarkStart','{'+W+'}bookmarkEnd','{'+M+'}oMath','{'+M+'}oMathPara'} for child in p):continue
        inlines=[]
        for child in p:
            if child.tag=='{'+W+'}r':
                value=''.join((t.text or '') if t.tag=='{'+W+'}t' else '\t' if t.tag=='{'+W+'}tab' else '\n' if t.tag in {'{'+W+'}br','{'+W+'}cr'} else '' for t in child)
                if not value:continue
                node={'id':id_('text'),'type':'text','text':value};style={}
                props=child.find('{'+W+'}rPr')
                if props is not None:
                    vert=props.find('{'+W+'}vertAlign')
                    if vert is not None and vert.get('{'+W+'}val') in {'subscript','superscript'}:style['vertical']=vert.get('{'+W+'}val')
                    for flag in ('b','i'):
                        f=props.find('{'+W+'}'+flag)
                        if f is not None:style['bold' if flag=='b' else 'italic']=f.get('{'+W+'}val','1') not in {'0','false','off'}
                if style:node['style']=style
                inlines.append(node)
            elif child.tag in {'{'+M+'}oMath','{'+M+'}oMathPara'}:
                math=child if child.tag=='{'+M+'}oMath' else child.find('{'+M+'}oMath')
                original=E.tostring(math,encoding='unicode')
                try:converted=native_formula(original)
                except Exception:continue
                ref=id_('source');sources[ref]={'latex':converted['latex'],'omml':original,'source_sha256':sha,'locator':f'word/document.xml#paragraph-{index}'}
                inlines.append({'id':id_('formula'),'type':'formula','latex':converted['latex'],'status':'restored','source_ref':ref})
        if not any(n['type']=='formula' for n in inlines):continue
        blocks.append({'id':id_('paragraph'),'type':'paragraph','align':'left','inlines':inlines});locators.append(index)
        if len(blocks)==2:break
    if len(blocks)!=2:raise RuntimeError('代表性原生公式片段不足')
    question={'id':'real-excerpt-q','revision':0,'number':'','qtype':'原件片段（未分题）','body':blocks,'options':[],'subquestions':[],'answers':[],'explanations':[],'source':{'source_sha256':sha,'number':'','locator':{'part':'word/document.xml','paragraphs':locators}},'origin':'import','issues':[]}
    bundle={'schema_version':1,'paper':{'id':'real-excerpt-paper','title':'真实原件公式片段','question_ids':[question['id']],'source_sha256':sha},'questions':[question],'assets':[],'issues':[]}
    assert validate_bundle(bundle)['ok'],validate_bundle(bundle)
    metadata={'kind':'real','scope':'two_source_paragraphs_not_whole_paper','source_path':str(source),'source_sha256':sha,'locators':locators,'expected_formulas':len(sources)}
    for name,value in [('bundle.json',bundle),('sources.json',sources),('provenance.json',metadata)]:
        (dest/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    store=ContentStore(ROOT/'.work/session-4/real-excerpt-store')
    for ref,record in sources.items():store.register_source(ref,record)
    result=export_paper(bundle,ROOT/'.work/session-4/outputs/real-excerpt.docx',{},AssetResolver(store))
    assert hashlib.sha256(source.read_bytes()).hexdigest()==sha
    print(json.dumps({'provenance':metadata,'report':result['report']},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
