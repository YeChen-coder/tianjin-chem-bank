import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from lxml import etree as E
from PIL import Image
from question_import_v2 import import_paper
from question_import_v2.importer import NS, W, M, R, block_text, walk_nodes
from question_import_v2.services import EvidenceServices
from question_content import validate_bundle

def p(text='', extra=''):
    node = E.Element('{' + W + '}p', nsmap=NS)
    if text:
        r = E.SubElement(node, '{' + W + '}r')
        E.SubElement(r, '{' + W + '}t').text = text
    if extra:
        wrapper = E.fromstring(('<wrapper ' + ' '.join(f'xmlns:{k}="{v}"' for k,v in NS.items()) + '>' + extra + '</wrapper>').encode())
        node.extend(list(wrapper))
    return node

def make_doc(path, elements, numbering=None, extra_parts=None):
    root = E.Element('{' + W + '}document', nsmap=NS)
    body = E.SubElement(root, '{' + W + '}body')
    body.extend(elements)
    with ZipFile(path, 'w') as z:
        z.writestr('word/document.xml', E.tostring(root))
        if numbering:
            z.writestr('word/numbering.xml', numbering)
        for name,data in (extra_parts or {}).items():
            z.writestr(name, data)

class ImportTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / '.work/session-1/tests'
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.root = Path(self.temp.name)
        self.source = self.root/'fixture.docx'
        self.services = EvidenceServices()

    def tearDown(self):
        self.temp.cleanup()

    def run_import(self, nodes, **kwargs):
        make_doc(self.source, nodes, **kwargs)
        before = hashlib.sha256(self.source.read_bytes()).hexdigest()
        result = import_paper(self.source, self.root/'output', self.services)
        self.assertEqual(before, hashlib.sha256(self.source.read_bytes()).hexdigest())
        validation = validate_bundle(result['bundle'])
        self.assertTrue(validation['ok'], validation)
        self.assertEqual([], validation['errors'])
        return result

    def test_options_and_nested_subquestion_answers(self):
        r = self.run_import([p('试卷说明：请仔细阅读'), p('一、选择题'), p('1. 哪个操作正确？'),
            p('A. 加热 B. 冷却 C. 过滤 D. 蒸发'), p('二、实验题'), p('2. 根据实验回答：'),
            p('（1）观察现象'), p('a) 记录颜色'), p('b) 记录气体'), p('（2）写出结论'),
            p('参考答案'), p('1. B'), p('2. （1）生成气体 （2）发生反应')])
        qs = r['bundle']['questions']
        self.assertEqual(['1','2'], [q['number'] for q in qs])
        self.assertEqual(['A','B','C','D'], [o['label'] for o in qs[0]['options']])
        self.assertEqual(['1','2'], [s['label'] for s in qs[1]['subquestions']])
        self.assertEqual(['a','b'], [s['label'] for s in qs[1]['subquestions'][0]['subquestions']])
        self.assertEqual([s['id'] for s in qs[1]['subquestions']], [a['target_id'] for a in qs[1]['answers']])
        self.assertEqual(qs[0]['id'], qs[0]['answers'][0]['target_id'])
        retained = json.loads(Path(r['report']['retained_content_path']).read_text(encoding='utf-8'))
        self.assertTrue(any('试卷说明' in block_text(x['block']) for x in retained))

    def test_zipped_non_ooxml_doc_uses_converter_and_keeps_original_hash(self):
        source=self.root/'old.doc'
        with ZipFile(source,'w') as z:z.writestr('Document.xml','legacy package')
        sha=hashlib.sha256(source.read_bytes()).hexdigest()
        calls=[]
        def convert(original,target):
            calls.append(original);make_doc(Path(target),[p('1. 下列化学实验操作正确的是？')])
        self.services.convert_doc=convert
        result=import_paper(source,self.root/'converted-output',self.services)
        self.assertEqual(len(calls),1)
        self.assertEqual(result['bundle']['paper']['source_sha256'],sha)
        self.assertEqual(len(result['bundle']['questions']),1)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),sha)

    def test_short_answer_key_rows_are_mapped_without_a_separate_heading(self):
        table=E.Element('{'+W+'}tbl',nsmap=NS);grid=E.SubElement(table,'{'+W+'}tblGrid')
        for _ in range(3):E.SubElement(grid,'{'+W+'}gridCol').set('{'+W+'}w','900')
        for values in [('题号','1','2'),('答案','A','B'),('题号','3'),('答案','C')]:
            row=E.SubElement(table,'{'+W+'}tr')
            for text in values:E.SubElement(row,'{'+W+'}tc').append(p(text))
        result=self.run_import([p('1. 下列说法正确的是？'),p('A. 甲 B. 乙 C. 丙 D. 丁'),p('2. 下列操作正确的是？'),p('A. 甲 B. 乙 C. 丙 D. 丁'),p('3. 下列实验正确的是？'),p('A. 甲 B. 乙 C. 丙 D. 丁'),table])
        for q,answer in zip(result['bundle']['questions'],'ABC'):
            self.assertTrue(q['answers']);self.assertIn(answer,block_text(q['answers'][0]['blocks'][0]))
            self.assertFalse(any(n['type']=='table' for n in walk_nodes(q['body'])))

    def test_prepared_package_keeps_raw_identity_and_source_layout_copy(self):
        source=self.root/'raw.doc';source.write_bytes(b'legacy original bytes')
        sha=hashlib.sha256(source.read_bytes()).hexdigest()
        cached=self.root/'prepared.docx';make_doc(cached,[p('1. 下列化学实验操作正确的是？')])
        self.services.prepare_package=lambda original:str(cached)
        result=import_paper(source,self.root/'prepared-output',self.services)
        self.assertEqual(result['bundle']['paper']['source_sha256'],sha)
        original=Path(result['report']['original_copy_path'])
        self.assertEqual(original.read_bytes(),source.read_bytes())
        self.assertEqual((original.parent/'converted.docx').read_bytes(),cached.read_bytes())
        self.assertEqual(len(result['bundle']['questions']),1)

    def test_mixed_parentheses_and_unknown_metal_formula_spans(self):
        result=self.run_import([p('1. 下列除杂方法错误的是？'),p('A. Cu（Fe) B. Ca(OH）2 C. CO2（O2） D. X(NO3)2+ZSO4')])
        formulas=[n['latex'] for n in walk_nodes(result['bundle']) if n['type']=='formula']
        self.assertIn(r'\mathrm{Cu}(\mathrm{Fe})',formulas)
        self.assertIn(r'\mathrm{Ca}{(\mathrm{O}\mathrm{H})}_{2}',formulas)
        self.assertIn(r'\mathrm{C}\mathrm{O}_{2}(\mathrm{O}_{2})',formulas)
        self.assertIn(r'\mathrm{X}{(\mathrm{N}\mathrm{O}_{3})}_{2}',formulas)
        self.assertIn(r'\mathrm{Z}\mathrm{S}\mathrm{O}_{4}',formulas)

    def test_bracket_normalization_does_not_convert_prose_or_unknown_elements(self):
        result=self.run_import([p('1. 下列说法正确的是？'),p('Cu（铁）与酸反应，Cu（Fe缺少右括号；Xx2不是有效元素；（填“左”或“右”）。')])
        self.assertFalse([n for n in walk_nodes(result['bundle']) if n['type']=='formula'])

    def test_selected_chemical_span_accepts_mixed_width_brackets(self):
        from question_import_v2._source_readers.text_formula import chemical_latex
        from formula_engine.text_formula import chemical_latex as editor_latex
        for convert in (chemical_latex,editor_latex):
            for text in ('Cu（Fe）','Cu（Fe)','Cu(Fe）'):
                self.assertEqual(convert([{'kind':'text','text':text}]),r'\mathrm{Cu}(\mathrm{Fe})')
            with self.assertRaises(ValueError):convert([{'kind':'text','text':'Cu（铁）'}])

    def test_future_import_cleans_bare_question_numbers_scores_and_identity_choices(self):
        table=E.Element('{'+W+'}tbl',nsmap=NS);grid=E.SubElement(table,'{'+W+'}tblGrid')
        for _ in range(4):E.SubElement(grid,'{'+W+'}gridCol').set('{'+W+'}w','900')
        row=E.SubElement(table,'{'+W+'}tr')
        for label in 'ABCD':E.SubElement(row,'{'+W+'}tc').append(p(label))
        result=self.run_import([p('63 根据图回答问题'),table,p('A. A B. B C. C D. D'),p('64（6分）下列说法正确的是？'),p('A. 甲 B. 乙 C. 丙 D. 丁')])
        first,second=result['bundle']['questions']
        self.assertEqual((first['number'],second['number']),('63','64'))
        self.assertEqual(first['options'],[]);self.assertEqual(len(second['options']),4)
        self.assertEqual(block_text(first['body'][0]),'根据图回答问题')
        self.assertNotIn('6分',block_text(second['body'][0]))

    def test_circle_enumeration_and_subquestion_options_stay_in_place(self):
        r = self.run_import([p('1. 现有①氦气 ②氧气，回答：'), p('（1）选择 A. 氦气 B. 氧气'),
                             p('A. 可以 B. 不可以'), p('（2）说明原因')])
        q = r['bundle']['questions'][0]
        self.assertEqual([], q['options'])
        self.assertEqual(['1','2'], [s['label'] for s in q['subquestions']])
        self.assertIn('①氦气', block_text(q['body'][0]))
        self.assertIn('A. 氦气', block_text(q['subquestions'][0]['blocks'][0]))

    def test_native_and_styled_formula_preserve_surrounding_text(self):
        formula = '<m:oMath><m:f><m:num><m:r><m:t>1</m:t></m:r></m:num><m:den><m:r><m:t>2</m:t></m:r></m:den></m:f></m:oMath>'
        script = '<w:r><w:t>H</w:t></w:r><w:r><w:rPr><w:vertAlign w:val="subscript"/></w:rPr><w:t>2</w:t></w:r><w:r><w:t>O，加入50 mL水。</w:t></w:r>'
        r = self.run_import([p('1. 数值为', formula + '<w:r><w:t>，样品为</w:t></w:r>' + script)])
        nodes = list(walk_nodes(r['bundle']['questions']))
        formulas = [n for n in nodes if n['type']=='formula']
        self.assertEqual(2, len(formulas))
        self.assertTrue(any('frac' in n['latex'] for n in formulas))
        self.assertTrue(any('_{2}' in n['latex'] for n in formulas))
        self.assertIn('50 mL水', ''.join(n.get('text','') for n in nodes))
        self.assertTrue(all(n['status']!='confirmed' for n in formulas))

    def test_formula_label_and_numeric_question_style(self):
        label = '<m:oMath><m:r><m:t>(1)</m:t></m:r></m:oMath><w:r><w:t>观察现象。</w:t></w:r>'
        r = self.run_import([p('1. 实验题：'), p('', label), p('(2) 得出结论。')])
        self.assertEqual(['1','2'], [s['label'] for s in r['bundle']['questions'][0]['subquestions']])

    def test_images_follow_inline_option_and_subquestion(self):
        buf = io.BytesIO(); Image.new('RGB',(12,6),'blue').save(buf,format='PNG')
        drawing = '<w:r><w:drawing><wp:inline><wp:extent cx="360000" cy="180000"/><a:blip r:embed="rImg"/></wp:inline></w:drawing></w:r>'
        rel = f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rImg" Target="media/image1.png"/></Relationships>'
        r = self.run_import([p('1. 看图选择'), p('A. 图一', drawing), p('B. 图二'), p('2. 看图回答'), p('(1) 观察',drawing)],
            extra_parts={'word/media/image1.png':buf.getvalue(), 'word/_rels/document.xml.rels':rel})
        q1,q2 = r['bundle']['questions']
        a = next(n for n in walk_nodes(q1['options'][0]) if n['type']=='image')
        b = next(n for n in walk_nodes(q2['subquestions'][0]) if n['type']=='image')
        self.assertEqual(a['asset_id'], b['asset_id'])
        self.assertNotEqual(a['id'], b['id'])
        self.assertEqual((10,5), (a['width_mm'],a['height_mm']))
        manifest=json.loads(Path(r['report']['asset_manifest_path']).read_text(encoding='utf-8'))
        self.assertEqual(hashlib.sha256(buf.getvalue()).hexdigest(), hashlib.sha256(Path(manifest[a['asset_id']]['local_path']).read_bytes()).hexdigest())

    def test_merged_and_nested_table_preserved(self):
        xml = '<w:tbl xmlns:w="' + W + '"><w:tblGrid><w:gridCol w:w="1440"/><w:gridCol w:w="1440"/></w:tblGrid><w:tr><w:tc><w:tcPr><w:vMerge w:val="restart"/></w:tcPr><w:p><w:r><w:t>合并格</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>上格</w:t></w:r></w:p></w:tc></w:tr><w:tr><w:tc><w:tcPr><w:vMerge/></w:tcPr><w:p/></w:tc><w:tc><w:tbl><w:tblGrid><w:gridCol w:w="720"/></w:tblGrid><w:tr><w:tc><w:p><w:r><w:t>嵌套内容</w:t></w:r></w:p></w:tc></w:tr></w:tbl><w:p/></w:tc></w:tr></w:tbl>'
        r = self.run_import([p('1. 分析表格'), E.fromstring(xml.encode())])
        table = r['bundle']['questions'][0]['body'][1]
        self.assertEqual(2, table['rows'][0]['cells'][0]['row_span'])
        self.assertEqual(1, len(table['rows'][1]['cells']))
        self.assertIn('嵌套内容', block_text(table))
        self.assertEqual(2, len([n for n in walk_nodes(table) if n['type']=='table']))

    def test_inline_answers_and_ambiguous_answer_not_dropped(self):
        r = self.run_import([p('一、选择题'),p('1. 第一题【答案】A【解析】依据性质'),
            p('二、选择题'),p('1. 第二题'),p('参考答案'),p('1. B'),p('后续解释仍需核对')])
        self.assertEqual(2, len(r['bundle']['questions']))
        q=r['bundle']['questions'][0]
        self.assertEqual('A', block_text(q['answers'][0]['blocks'][0]))
        self.assertEqual('依据性质', block_text(q['explanations'][0]['blocks'][0]))
        retained=json.loads(Path(r['report']['retained_content_path']).read_text(encoding='utf-8'))
        self.assertTrue(any('1. B' in block_text(x['block']) for x in retained))
        self.assertTrue(any(x['code']=='ANSWER_MAPPING_REVIEW' for x in r['bundle']['issues']))

    def test_unsupported_math_and_unknown_blocks_remain_visible(self):
        r=self.run_import([p('1. 前文', '<m:oMath><m:unknown><m:r><m:t>x</m:t></m:r></m:unknown></m:oMath><w:r><w:t>后文</w:t></w:r>'),
            E.fromstring(('<w:altChunk xmlns:w="'+W+'"/>').encode())])
        q=r['bundle']['questions'][0]
        formula=next(n for n in walk_nodes(q) if n['type']=='formula')
        self.assertIsNone(formula['latex'])
        self.assertEqual('unsupported',formula['status'])
        self.assertEqual('unresolved',q['body'][1]['type'])
        records=json.loads(Path(r['report']['source_records_path']).read_text(encoding='utf-8'))
        self.assertIn('unknown', records[formula['source_ref']]['xml'])
        self.assertIn('后文',block_text(q['body'][0]))

    def test_numbering_and_repeat_ids(self):
        numbering=f'<w:numbering xmlns:w="{W}"><w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl></w:abstractNum><w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>'
        nodes=[p('观察实验'),p('分析数据')]
        for node in nodes:
            prop=E.Element('{'+W+'}pPr'); num=E.SubElement(prop,'{'+W+'}numPr')
            E.SubElement(num,'{'+W+'}numId').set('{'+W+'}val','1'); node.insert(0,prop)
        r=self.run_import(nodes,numbering=numbering)
        second=import_paper(self.source,self.root/'second',self.services)
        self.assertEqual(['1','2'],[q['number'] for q in r['bundle']['questions']])
        self.assertEqual(r['bundle'],second['bundle'])
        path=Path(r['report']['boundary_report_path']).with_name('import-result.json')
        receipt=hashlib.sha256(path.read_bytes()).hexdigest()
        same=import_paper(self.source,self.root/'output',self.services)
        self.assertTrue(same['report']['reused'])
        self.assertEqual(receipt,hashlib.sha256(path.read_bytes()).hexdigest())

    def test_number_followed_by_year_and_mixed_math_label(self):
        mixed='<m:oMath><m:r><m:t>(1)141 g</m:t></m:r></m:oMath><w:r><w:t>样品中有多少氧？</w:t></w:r>'
        r=self.run_import([p('4.2026年发生的变化'),p('5. 计算题'),p('',mixed),p('(2) 说明原因')])
        self.assertEqual(['4','5'],[q['number'] for q in r['bundle']['questions']])
        self.assertEqual(['1','2'],[s['label'] for s in r['bundle']['questions'][1]['subquestions']])
        formula=next(n for n in walk_nodes(r['bundle']['questions'][1]) if n['type']=='formula')
        self.assertIn('141',formula['latex'])
        self.assertNotIn('(1)',formula['latex'])

    def test_unmarked_repeated_labels_are_retained_for_review(self):
        r=self.run_import([p('1. 填空'),p('(1) 第一问 (2) 第二问'),p('(1) 解释第一问 (2) 解释第二问')])
        q=r['bundle']['questions'][0]
        self.assertEqual(['1','2'],[s['label'] for s in q['subquestions']])
        self.assertIn('解释第一问',block_text(q['subquestions'][1]['blocks'][1]))
        self.assertTrue(any(i['code']=='ANSWER_MAPPING_REVIEW' for i in q['issues']))

    def test_roman_subsections_own_numeric_children(self):
        r=self.run_import([p('1. 航母题'),p('Ⅰ．建造'),p('(1) 金属'),p('Ⅱ．能源'),p('(2) 电池')])
        q=r['bundle']['questions'][0]
        self.assertEqual(['Ⅰ','Ⅱ'],[s['label'] for s in q['subquestions']])
        self.assertEqual('1',q['subquestions'][0]['subquestions'][0]['label'])
        self.assertEqual('2',q['subquestions'][1]['subquestions'][0]['label'])

    def test_answer_circle_enumeration_keeps_parent_target(self):
        r=self.run_import([p('1. 实验'),p('(1) 填两个空'),p('【答案】(1) ①. 氧气 ②. 水')])
        q=r['bundle']['questions'][0]
        self.assertEqual(1,len(q['answers']))
        self.assertEqual(q['subquestions'][0]['id'],q['answers'][0]['target_id'])
        self.assertIn('①.', ''.join(block_text(b) for b in q['answers'][0]['blocks']))

    def test_doc_converter_and_missing_converter(self):
        source=self.root/'legacy.doc';source.write_bytes(b'not a ZIP legacy sample')
        r=import_paper(source,self.root/'doc-output',self.services)
        self.assertEqual('unsupported_source',r['report']['status'])
        self.assertEqual([],r['bundle']['questions'])
        self.assertTrue(Path(r['report']['original_copy_path']).exists())
        class Converter(EvidenceServices):
            def convert_doc(self,src,dst):
                make_doc(dst,[p('1. 转换得到的内容')])
        result=import_paper(source,self.root/'converted-output',Converter())
        self.assertEqual(1,result['report']['question_count'])
        validation = validate_bundle(result['bundle'])
        self.assertTrue(validation['ok'], validation)
        self.assertEqual([], validation['errors'])

if __name__=='__main__':
    unittest.main()
