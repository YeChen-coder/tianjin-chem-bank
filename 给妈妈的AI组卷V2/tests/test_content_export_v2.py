import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
from zipfile import ZipFile
from lxml import etree as E

from question_content import validate_bundle,validate_question,compile_formula
from question_content.compiler import _compile
from question_content.http_api import AssetResolver,ContentAPI
from question_content.storage import ContentError,ContentStore,nodes
from word_export_v2 import export_paper
from formula_engine.engine import _nodes,normalize_parts

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=ROOT/'fixtures/word_latex_v1/synthetic'
NS={'m':'http://schemas.openxmlformats.org/officeDocument/2006/math','w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}


class ContentTests(unittest.TestCase):
    def setUp(self):
        base=ROOT/'.work/session-4/tests';base.mkdir(parents=True,exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=base);self.path=Path(self.tmp.name)
        self.bundle=json.loads((FIXTURE/'bundle.json').read_text(encoding='utf-8'))
        self.store=ContentStore(self.path/'store')
        self.store.register_asset(FIXTURE/'apparatus.png');self.store.put_bundle(self.bundle)
    def tearDown(self):self.tmp.cleanup()
    def codes(self,bundle):return {e['code'] for e in validate_bundle(bundle)['errors']}

    def test_fixture_valid_nonmutating(self):
        before=copy.deepcopy(self.bundle);self.assertTrue(validate_bundle(self.bundle)['ok']);self.assertEqual(before,self.bundle)
        self.assertTrue(validate_question(self.bundle['questions'][0],self.bundle['assets'])['ok'])

    def test_export_uses_new_selection_numbers_and_never_original_numbers(self):
        bundle=copy.deepcopy(self.bundle);q=bundle['questions'][0];q['number']='63';q['body']=[{'id':'number-test-p','type':'paragraph','inlines':[{'id':'number-test-t','type':'text','text':'根据图回答问题'}],'align':'left'}];q['options']=[];q['subquestions']=[];q['answers']=[];q['explanations']=[];q['issues']=[]
        bundle['questions']=[q];bundle['paper']['question_ids']=[q['id']];bundle['issues']=[];bundle['paper'].pop('annotations',None)
        for autonum,expected in [(True,'1. 根据图回答问题'),(False,'根据图回答问题')]:
            path=self.path/('number-'+str(autonum)+'.docx');export_paper(bundle,path,{'auto_number':autonum},AssetResolver(self.store))
            with ZipFile(path) as z:root=E.fromstring(z.read('word/document.xml'))
            text=[''.join(p.xpath('.//w:t/text()',namespaces=NS)) for p in root.xpath('//w:body/w:p',namespaces=NS)]
            self.assertIn(expected,text);self.assertFalse(any(t.startswith('63.') for t in text))
    def test_duplicate_ids_across_questions(self):
        self.bundle['questions'][1]['body'][0]['id']=self.bundle['questions'][0]['body'][0]['id']
        self.assertIn('DUPLICATE_ID',self.codes(self.bundle))
    def test_answer_cannot_target_other_question(self):
        self.bundle['questions'][1]['answers'][0]['target_id']='q-control-1'
        self.assertIn('ANSWER_MAPPING_REVIEW',self.codes(self.bundle))
    def test_missing_asset_rejected(self):
        self.bundle['assets']=[];self.assertIn('ASSET_MISSING',self.codes(self.bundle))
    def test_asset_identity_does_not_depend_on_second_filename(self):
        renamed=self.path/'same-bytes.bin';renamed.write_bytes((FIXTURE/'apparatus.png').read_bytes())
        self.assertEqual(self.store.register_asset(renamed),self.bundle['assets'][0])
    def test_overlapping_table(self):
        table=self.bundle['questions'][2]['body'][1];table['rows'][2]['cells'][0]['col_index']=0
        self.assertIn('TABLE_OVERLAP',self.codes(self.bundle))
    def test_table_hole(self):
        self.bundle['questions'][2]['body'][1]['rows'][2]['cells'].pop()
        self.assertIn('TABLE_GAP',self.codes(self.bundle))
    def test_unknown_fields_do_not_disappear(self):
        self.bundle['questions'][0]['body'][0]['inlines'][0]['html']='<script/>'
        self.assertIn('UNKNOWN_FIELD',self.codes(self.bundle))
    def test_invalid_enum_type_reported(self):
        self.bundle['questions'][0]['origin']={};self.assertIn('ENUM',self.codes(self.bundle))
    def test_compiler_preserves_reaction_condition(self):
        latex=r'\mathrm{CaCO}_{3}\overset{900^{\circ}\mathrm{C}}{=}\mathrm{CaO}+\mathrm{CO}_{2}\uparrow'
        result=compile_formula(latex);self.assertTrue(result['ok'],result)
        tree=E.fromstring(result['omml'].encode());self.assertTrue(tree.xpath('//m:limUpp//m:lim//m:t[text()="900"]',namespaces=NS))
    def test_compiler_failure_is_explicit(self):
        result=compile_formula(r'\unsupported{NaOH}','source-x');self.assertFalse(result['ok']);self.assertIsNone(result['omml']);self.assertTrue(result['diagnostics'])
    def test_unbalanced_formula_rejected(self):
        self.assertFalse(compile_formula(r'x^{2')['ok']);self.assertFalse(compile_formula(r'x}')['ok'])
        self.assertTrue(compile_formula(r'\{x\}')['ok'])
    def test_portable_subset_and_explicit_failure(self):
        from unittest.mock import patch
        with patch('formula_engine.engine.office_xsl',return_value=None):
            self.assertTrue(compile_formula(r'\mathrm{H}_{2}\mathrm{O}')['ok'])
            self.assertFalse(compile_formula(r'\begin{matrix}a&b\\c&d\end{matrix}')['ok'])
    def test_legacy_original_roundtrip_and_metadata(self):
        from question_content.legacy import question_from_legacy,legacy_segments_from_question
        original='[[{"t":"text","s":"H"},{"t":"text","s":"2","vert":"subscript"},{"t":"text","s":"O"}]]'
        row={'id':42,'qnum':'7','qtype':'填空题','segments':original,'body':'H2O','answer':'水','category_manual':1}
        result=question_from_legacy(row,self.store,metadata={'classification':['物质'],'review':'teacher'})
        q=result['question'];self.assertEqual(legacy_segments_from_question(q,self.store),original)
        self.assertEqual(self.store.resolve_source('legacy-42-legacy-original')['metadata']['review'],'teacher')
        q['body'][0]['inlines'][0]['text']='改动'
        with self.assertRaises(ContentError):legacy_segments_from_question(q,self.store)
    def test_changed_formula_invalidates_cache(self):
        a=compile_formula(r'x^{2}');b=compile_formula(r'x^{3}');self.assertNotEqual(a['omml'],b['omml'])
        b['omml']='mutated';self.assertNotEqual(compile_formula(r'x^{3}')['omml'],'mutated')
    def test_repeated_native_parts_have_unique_ids(self):
        omml=compile_formula(r'x^{2}')['omml'];part={'t':'text','s':'x2','omml':omml}
        items=_nodes([part,part],self.path);self.assertNotEqual(items[0]['id'],items[1]['id'])
    def test_failed_native_formula_preserved(self):
        part={'t':'text','s':'H2O','omml':'<broken'}
        out=normalize_parts([part],self.path);self.assertEqual(out[0]['omml'],'<broken');self.assertEqual(out[0]['s'],'H2O');self.assertIn('formula_issue',out[0])
    def test_recovered_image_formula_has_search_text(self):
        result=compile_formula(r'\mathrm{H}_{2}\mathrm{O}');reference=self.path/'references';reference.mkdir()
        (reference/'abc.json').write_text(json.dumps({'formulas':[result]}),encoding='utf-8')
        out=normalize_parts([{'t':'img','sha':'abc'}],self.path);self.assertTrue(out[0]['s']);self.assertIn('source_image',out[0])
    def test_atomic_save_and_conflict(self):
        q=copy.deepcopy(self.bundle['questions'][0]);q['body'][0]['inlines'][0]['text']='教师修改'
        saved=self.store.save_question(q['id'],0,q)['question'];self.assertEqual(saved['revision'],1)
        with self.assertRaises(ContentError) as ctx:self.store.save_question(q['id'],0,q)
        self.assertEqual(ctx.exception.status,409);self.assertEqual(self.store.get_question(q['id'])['question'],saved)
    def test_two_writers_only_one_wins(self):
        q=copy.deepcopy(self.bundle['questions'][0])
        def save(_):
            try:self.store.save_question(q['id'],0,q);return 200
            except ContentError as exc:return exc.status
        with ThreadPoolExecutor(max_workers=2) as pool:self.assertEqual(sorted(pool.map(save,[1,2])),[200,409])
    def test_protected_metadata_rejected(self):
        q=copy.deepcopy(self.bundle['questions'][0]);q['source']={'fake':'path'}
        with self.assertRaises(ContentError):self.store.save_question(q['id'],0,q)
        self.assertEqual(self.store.get_question(q['id'])['question']['revision'],0)
    def test_client_cannot_confirm_new_formula(self):
        q=copy.deepcopy(self.bundle['questions'][0]);next(n for n in nodes(q) if n.get('type')=='formula')['status']='confirmed'
        with self.assertRaises(ContentError):self.store.save_question(q['id'],0,q)
    def test_snapshot_version_conflict(self):
        with self.assertRaises(ContentError) as ctx:self.store.snapshot('paper-controls',{})
        self.assertEqual(ctx.exception.status,409)
    def test_native_export_structure_and_media(self):
        result=export_paper(self.bundle,self.path/'paper.docx',{'repeat_table_header':True},AssetResolver(self.store))
        with ZipFile(result['output_path']) as z:
            tree=E.fromstring(z.read('word/document.xml'))
            self.assertEqual(len(tree.xpath('//m:oMath',namespaces=NS)),result['report']['formula_count'])
            self.assertEqual(len(tree.xpath('//w:tbl',namespaces=NS)),2)
            self.assertTrue(tree.xpath('//w:gridSpan[@w:val="3"]',namespaces=NS));self.assertTrue(tree.xpath('//w:vMerge[@w:val="restart"]',namespaces=NS))
            self.assertTrue(tree.xpath('//w:vertAlign[@w:val="subscript"]',namespaces=NS));self.assertTrue(tree.xpath('//w:fitText',namespaces=NS))
            media=[hashlib.sha256(z.read(name)).hexdigest() for name in z.namelist() if name.startswith('word/media/')]
            self.assertIn(self.bundle['assets'][0]['sha256'],media)
        self.assertTrue(result['report']['ok']);self.assertFalse(result['report']['visual_verified'])
    def test_edited_version_export(self):
        q=copy.deepcopy(self.bundle['questions'][0]);f=next(n for n in nodes(q) if n.get('type')=='formula' and '900' in n['latex']);f['latex']=f['latex'].replace('900','910')
        self.store.save_question(q['id'],0,q)
        revisions={q['id']:q['revision'] for q in self.store.snapshot('paper-controls')['questions']}
        result=export_paper(self.store.snapshot('paper-controls',revisions),self.path/'edited.docx',{},AssetResolver(self.store))
        with ZipFile(result['output_path']) as z:
            tree=E.fromstring(z.read('word/document.xml'));self.assertTrue(tree.xpath('//m:t[text()="910"]',namespaces=NS));self.assertFalse(tree.xpath('//m:t[text()="900"]',namespaces=NS))
        self.assertEqual(result['report']['revisions']['q-control-1'],1)
    def test_unsupported_preserves_marker_and_report(self):
        f=next(n for n in nodes(self.bundle) if n.get('type')=='formula');f['latex']=r'\unsupported{x}'
        result=export_paper(self.bundle,self.path/'fallback.docx',{},AssetResolver(self.store));self.assertFalse(result['report']['ok'])
        with ZipFile(result['output_path']) as z:self.assertIn('待核对公式',z.read('word/document.xml').decode())
    def test_strict_export_does_not_publish(self):
        f=next(n for n in nodes(self.bundle) if n.get('type')=='formula');f['latex']=r'\unsupported{x}'
        with self.assertRaises(ContentError):export_paper(self.bundle,self.path/'strict.docx',{'fail_on_issues':True},AssetResolver(self.store))
        self.assertFalse((self.path/'strict.docx').exists())
    def test_tampered_media_reported(self):
        self.store.asset(self.bundle['assets'][0]['id']).write_bytes(b'corrupt')
        result=export_paper(self.bundle,self.path/'missing.docx',{},AssetResolver(self.store));self.assertFalse(result['report']['ok']);self.assertTrue(any(i['code']=='ASSET_MISSING' for i in result['report']['issues']))
    def test_source_native_retained_until_latex_changed(self):
        f=next(n for n in nodes(self.bundle) if n.get('type')=='formula');f['source_ref']='native-1';f['latex']=None;f['status']='review_required'
        self.store.register_source('native-1',{'omml':compile_formula(r'x^{2}')['omml'],'latex':r'x^{2}'})
        result=export_paper(self.bundle,self.path/'native.docx',{},AssetResolver(self.store));self.assertEqual(result['report']['native_source_count'],1)
        f['latex']=r'x^{3}';result=export_paper(self.bundle,self.path/'changed.docx',{},AssetResolver(self.store));self.assertEqual(result['report']['native_source_count'],0)


class HTTPTests(unittest.TestCase):
    setUp=ContentTests.setUp
    tearDown=ContentTests.tearDown
    def test_http_contract(self):
        import app
        original=app.CONTENT_API;app.CONTENT_API=ContentAPI(self.path/'store',ROOT)
        server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        def request(path,payload=None,method=None):
            req=Request(base+path,data=json.dumps(payload).encode() if payload is not None else None,method=method,headers={'Content-Type':'application/json'})
            try:
                with urlopen(req) as r:return r.status,json.loads(r.read())
            except HTTPError as exc:return exc.code,json.loads(exc.read())
        try:
            status,data=request('/api/v2/content/questions/q-control-1');self.assertEqual(status,200)
            status,data=request('/api/v2/content/formulas/compile',{'latex':r'x^{2}','request_id':'r1'});self.assertEqual(status,200);self.assertEqual(data['request_id'],'r1')
            status,data=request('/api/v2/content/formulas/compile',{'latex':r'\unsupported{x}','request_id':'r2'});self.assertEqual(status,422);self.assertEqual(data['request_id'],'r2');self.assertEqual(data['error']['code'],'FORMULA_COMPILE_FAILED')
            q=self.bundle['questions'][0];status,data=request('/api/v2/content/questions/'+q['id'],{'schema_version':1,'expected_revision':0,'question':q},'PUT');self.assertEqual(status,200)
            status,data=request('/api/v2/content/questions/'+q['id'],{'schema_version':1,'expected_revision':0,'question':q,'request_id':'conflict'},'PUT');self.assertEqual(status,409);self.assertEqual(data['request_id'],'conflict')
            status,data=request('/api/v2/content/papers/paper-controls/export',{'expected_revisions':{q['id']:0 for q in self.bundle['questions']}});self.assertEqual(status,409)
            snapshot=self.store.snapshot('paper-controls');status,data=request('/api/v2/content/papers/paper-controls/export',{'expected_revisions':{q['id']:q['revision'] for q in snapshot['questions']}});self.assertEqual(status,200)
            with urlopen(base+data['download_url']) as r:self.assertTrue(r.read().startswith(b'PK'))
            status,data=request('/api/v2/content/questions/not-found');self.assertEqual(status,404);self.assertIn('details',data['error'])
        finally:server.shutdown();thread.join();server.server_close();app.CONTENT_API=original


if __name__=='__main__':unittest.main()
