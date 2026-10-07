import base64
import json
from pathlib import Path
import sys
import zipfile
from xml.etree import ElementTree as ET

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import banklib as b
import question_analysis as qa

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'


def p(text):
    from xml.sax.saxutils import escape
    return '<w:p><w:r><w:t xml:space="preserve">' + escape(text) + '</w:t></w:r></w:p>'


def document(tmp_path, blocks, extra=None):
    path = tmp_path / 'paper.docx'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('word/document.xml', '<w:document ' + NS + '><w:body>' + blocks + '</w:body></w:document>')
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return path


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(b, 'DB_PATH', str(tmp_path / 'bank.sqlite'))
    monkeypatch.setattr(b, 'DATA_DIR', str(tmp_path))
    monkeypatch.setattr(b, 'MEDIA', str(tmp_path / 'media'))
    monkeypatch.setattr(b, 'MEDIA_ORIG', str(tmp_path / 'media/original'))
    monkeypatch.setattr(b, 'IMPORT_DIR', str(tmp_path / 'imports'))
    b._image_cache.clear()


@pytest.mark.parametrize('stem,section,expected', [
    ('如图有装置 A、B、C、D，请解释装置的作用。', None, '实验题'),
    ('某小组设计实验\nA. 猜想\nB. 验证\nC. 观察\nD. 结论', '简答题', '简答题'),
    ('下列实验操作正确的是\nA. 甲\nB. 乙\nC. 丙\nD. 丁', None, '单选题'),
    ('正确的有\nA. 甲\nB. 乙\nC. 丙\nD. 丁', None, '多选题'),
    ('（1）填写____。（2）从下列选项选出答案：\nA. 甲\nB. 乙\nC. 丙\nD. 丁', None, '填空题'),
    ('计算生成氢气的质量____。', None, '计算题'),
    ('说明为什么生锈。', '简答题', '简答题'),
    ('图中只有装置标号\nA | B | C | D\n（1）填写____（2）原因____', '实验题', '实验题'),
])
def test_type(stem, section, expected):
    assert qa.qtype_info(stem, section)['qtype'] == expected


def test_glued_first_paragraph(tmp_path):
    qs = b.questions_from_docx(document(tmp_path, p('1．请说明空气中的主要成分。\n2．请解释水的组成及测定方法。\n3．请写出氧气制取的化学方程式。')))
    assert [q['qnum'] for q in qs] == ['1', '2', '3']


def test_numbered_exam_instructions_and_mass_lists_are_not_questions(tmp_path):
    blocks = (p('1．每题选出答案后，用2B铅笔填涂答题卡。')
              + p('2．本卷共10题，共30分。')
              + p('3．可能用到的相对原子质量：H 1__C 12__O 16')
              + p('一、单选题')
              + p('1．空气中含量最多的气体是（  ）')
              + p('A．氧气 B．氮气 C．氢气 D．二氧化碳'))
    qs = b.questions_from_docx(document(tmp_path, blocks))
    assert len(qs) == 1 and '空气中' in qs[0]['body']
    assert '答题卡' not in qs[0]['body']


def test_mass_reference_attached_to_a_real_question_is_preserved():
    assert not b.is_exam_notice('可能用到的相对原子质量：H 1 O 16\n1．计算水的相对分子质量。')
    assert not b.is_exam_notice('已知相对原子质量H 1 O 16，求水的相对分子质量。')


@pytest.mark.parametrize('number', [4, 57, 208])
@pytest.mark.parametrize('stem', [
    '已知相对原子质量H=1，O=16，计算水的相对分子质量。',
    '某元素的相对原子质量为24，写出该元素的符号。',
    '相对原子质量与原子实际质量有什么区别？',
    '可能用到的相对原子质量：H 1 O 16，计算水的相对分子质量。',
])
def test_new_mass_questions_survive_actual_docx_import(tmp_path, number, stem):
    qs = b.questions_from_docx(document(tmp_path, p(str(number) + '．' + stem)))
    assert len(qs) == 1
    assert qs[0]['qnum'] == str(number)
    assert qs[0]['body'] == stem


def test_subquestions_decimal_and_section(tmp_path):
    qs = b.questions_from_docx(document(tmp_path, p('三、简答题（本大题共3题）') + p('16．请回答下列问题。\n（1）为什么？\n（2）如何验证？\n1.5g 是样品质量。') + p('17．请说明空气污染的主要原因。')))
    assert [q['qnum'] for q in qs] == ['16', '17']
    assert qs[0]['qtype'] == '简答题'
    assert '1.5g' in qs[0]['body']


def test_export_rich_existing_answer_keeps_formula_image_and_table(tmp_path):
    con = b.init_db()
    qs = b.questions_from_docx(document(tmp_path, p('1．填写水的化学式，并说明实验依据。')))
    _, _, ids = b.insert_questions(con, 'answer-fixture.docx', qs)
    qid = ids[0]
    image_name = 'a'*64+'.png'
    Path(b.MEDIA).mkdir(parents=True, exist_ok=True)
    (Path(b.MEDIA)/image_name).write_bytes(PNG)
    formula = '<m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"><m:r><m:t>SO₄²⁻</m:t></m:r></m:oMath>'
    blocks = [[{'t':'text','s':'【答案】H'}, {'t':'text','s':'2','vert':'subscript'}, {'t':'text','s':'O'},
               {'t':'text','s':'SO₄²⁻','omml':formula}],
              [{'t':'img','src':'/media/'+image_name,'sha':'a'*64}],
              {'t':'table','rows':[[[{'t':'text','s':'原有解析表格'}],[{'t':'text','s':'正确现象'}]]]}]
    answer = '\n'.join(b.item_plain(block) for block in blocks).strip()
    metadata = json.loads(con.execute('SELECT payload FROM question_metadata WHERE question_id=?', (qid,)).fetchone()[0])
    metadata['answer_segments'] = blocks
    con.execute('UPDATE question_metadata SET payload=? WHERE question_id=?', (json.dumps(metadata), qid))
    con.execute('UPDATE questions SET answer=? WHERE id=?', (answer, qid))
    con.commit(); con.close()
    for include in (False, True):
        out = tmp_path/('answers.docx' if include else 'questions-only.docx')
        b.export_docx(ids, str(out), keep_answers=include)
        with zipfile.ZipFile(out) as archive:
            xml = archive.read('word/document.xml')
            root = ET.fromstring(xml)
            images = [name for name in archive.namelist() if name.startswith('word/media/')]
            assert bool(images) == include
            if include:
                assert archive.read(images[0]) == PNG
        assert (root.find('.//'+b.M+'oMath') is not None) == include
        assert (root.find('.//'+b.W+'tbl') is not None) == include
        assert ('【答案】'.encode('utf-8') in xml) == include
        if include:
            assert any(node.get(b.W+'val') == 'subscript' for node in root.findall('.//'+b.W+'vertAlign'))
    con = b.open_db()
    assert con.execute('SELECT answer FROM questions WHERE id=?', (qid,)).fetchone()[0] == answer
    con.close()


def test_export_uses_current_answer_instead_of_stale_imported_answer(tmp_path):
    qs = b.questions_from_docx(document(tmp_path, p('1．说明水的组成及实验依据。') + p('【答案】旧答案内容')))
    con = b.init_db(); _, _, ids = b.insert_questions(con, 'old.docx', qs)
    con.execute('UPDATE questions SET answer=? WHERE id=?', ('教师保存的新答案', ids[0]))
    con.commit(); con.close()
    out = tmp_path/'updated.docx'
    b.export_docx(ids, str(out), keep_answers=True)
    with zipfile.ZipFile(out) as archive:
        xml = archive.read('word/document.xml').decode('utf-8')
    assert '教师保存的新答案' in xml and '旧答案内容' not in xml


def test_separate_answers(tmp_path):
    qs = b.questions_from_docx(document(tmp_path, p('1．说明水的组成及依据。') + p('2．说明氧气的制取方法。') + p('参考答案') + p('1．氢元素和氧元素。') + p('2．用过氧化氢制取。')))
    assert len(qs) == 2
    assert '氢元素' in qs[0]['answer'] and '过氧化氢' in qs[1]['answer']


def test_question_table_and_option_table(tmp_path):
    table = '<w:tbl><w:tr><w:tc>' + p('1．说明金属锈蚀的条件。') + '</w:tc><w:tc>' + p('2．说明金属防护的措施。') + '</w:tc></w:tr></w:tbl>'
    assert len(b.questions_from_docx(document(tmp_path, table))) == 2
    options = '<w:tbl>' + ''.join('<w:tr><w:tc>' + p(letter) + '</w:tc><w:tc>' + p('实验方案') + '</w:tc></w:tr>' for letter in 'ABCD') + '</w:tbl>'
    qs = b.questions_from_docx(document(tmp_path, p('一、选择题') + p('1．下列实验方案正确的是。') + options))
    assert len(qs) == 1 and qs[0]['qtype'] == '单选题'
    assert qs[0]['segments'][1]['t'] == 'table'


def test_auto_numbering(tmp_path):
    numbering = '<w:numbering ' + NS + '><w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl></w:abstractNum><w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>'
    def numbered(text):
        return p(text).replace('<w:p>', '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>')
    qs = b.questions_from_docx(document(tmp_path, numbered('请写出水的化学式。') + numbered('请写出氢气的化学式。'), {'word/numbering.xml': numbering}))
    assert [q['qnum'] for q in qs] == ['1', '2']


def test_picture_at_question_boundary(tmp_path):
    drawing = '<w:r><w:drawing><a:blip r:embed="rId1"/></w:drawing></w:r>'
    blocks = p('1．根据图像说明实验现象。\n2．根据图像说明实验原理。').replace('</w:p>', drawing + '</w:p>')
    rel = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="media/p.png"/></Relationships>'
    qs = b.questions_from_docx(document(tmp_path, blocks, {'word/_rels/document.xml.rels': rel, 'word/media/p.png': PNG}))
    assert len(qs) == 2 and len(qs[1]['images']) == 1
    assert not qs[0]['images']


def test_formulas_survive_export(tmp_path):
    formula = '<m:oMath><m:sSup><m:e><m:r><m:t>SO</m:t></m:r></m:e><m:sup><m:r><m:t>2-</m:t></m:r></m:sup></m:sSup></m:oMath>'
    subs = '<w:r><w:rPr><w:vertAlign w:val="subscript"/></w:rPr><w:t>2</w:t></w:r>'
    block = '<w:p><w:r><w:t>1．填写化学式 H</w:t></w:r>' + subs + '<w:r><w:t>O 和离子符号：</w:t></w:r>' + formula + '</w:p>'
    qs = b.questions_from_docx(document(tmp_path, block))
    con = b.init_db()
    _, _, ids = b.insert_questions(con, 'fixture.docx', qs)
    con.commit(); con.close()
    out = tmp_path / 'export.docx'
    assert b.export_docx(ids, str(out)) == 1
    with zipfile.ZipFile(out) as archive:
        root = ET.fromstring(archive.read('word/document.xml'))
    assert root.find('.//' + b.M + 'oMath') is not None
    assert any(node.get(b.W + 'val') == 'subscript' for node in root.findall('.//' + b.W + 'vertAlign'))


def test_superscript_export(tmp_path):
    qs = b.questions_from_docx(document(tmp_path, p('1．离子 SO4^{2-} 的符号如何解释？')))
    con = b.init_db(); _, _, ids = b.insert_questions(con, 'ions.docx', qs); con.commit(); con.close()
    out = tmp_path / 'ions.docx'; b.export_docx(ids, str(out))
    with zipfile.ZipFile(out) as archive:
        xml = archive.read('word/document.xml').decode()
    assert 'superscript' in xml and '^{2-}' not in xml


def test_multi_labels_manual_locks_and_metadata(tmp_path):
    qs = b.questions_from_docx(document(tmp_path, p('二、实验探究题') + p('16．用高锰酸钾制取氧气，探究质量守恒，并写出化学方程式。')))
    con = b.init_db(); _, _, ids = b.insert_questions(con, 'combined.docx', qs); con.commit()
    qid = ids[0]
    row = con.execute('SELECT * FROM questions WHERE id=?', (qid,)).fetchone()
    con.row_factory = __import__('sqlite3').Row
    row = con.execute('SELECT * FROM questions WHERE id=?', (qid,)).fetchone()
    majors, minors = b.load_labels(con, qid, row['major'], row['minor'])
    assert '制取氧气' in minors and '质量守恒定律' in minors
    assert len(b.question_metadata(con, row)['knowledge']['themes']) >= 2
    b.assign_qtype(con, qid, {'qtype':'简答题'})
    b.insert_questions(con, 'another.docx', qs); con.commit()
    assert con.execute('SELECT qtype FROM questions WHERE id=?', (qid,)).fetchone()[0] == '简答题'
    con.close()


def test_missing_picture_reports_and_blocks_export(tmp_path):
    qs = b.questions_from_docx(document(tmp_path, p('1．根据装置图回答下列问题。').replace('</w:p>', '<w:r><w:drawing><a:blip r:embed="missing"/></w:drawing></w:r></w:p>')))
    assert any('图片关系' in w for w in qs[0]['warnings'])
    qs[0]['segments'].append([{'t':'img','src':'/media/missing.png','sha':'missing'}])
    con = b.init_db(); _, _, ids = b.insert_questions(con, 'missing.docx', qs); con.commit(); con.close()
    with pytest.raises(RuntimeError, match='图片|公式'):
        b.export_docx(ids, str(tmp_path / 'broken.docx'))


def test_ai_disabled_does_not_read_keys_or_start(monkeypatch):
    import aivariant
    monkeypatch.setenv('CHEM_DISABLE_AI', '1')
    monkeypatch.setattr(aivariant, '_started', False)
    aivariant.start_worker()
    assert not aivariant._started
    with pytest.raises(aivariant.ModelError):
        aivariant.complete_role('flash', '', '')


def test_score_number_without_punctuation(tmp_path):
    qs = b.questions_from_docx(document(tmp_path, p('四、实验题') + p('24（6分）实验桌上有一瓶溶液，请进行实验探究。') + p('25．计算生成氧气的质量。')))
    assert [q['qnum'] for q in qs] == ['24', '25']


def test_original_vector_is_embedded(tmp_path):
    from word_export import add_vector
    from docx import Document
    from docx.shared import Inches
    original = tmp_path / 'diagram.emf'
    original.write_bytes(b'original-metafile-fixture')
    doc = Document()
    add_vector(doc.add_paragraph(), str(original), Inches(2))
    out = tmp_path / 'vector.docx'; doc.save(out)
    with zipfile.ZipFile(out) as archive:
        assert archive.read('word/media/diagram.emf') == original.read_bytes()
        assert b'image/x-emf' in archive.read('[Content_Types].xml')
        assert b'media/diagram.emf' in archive.read('word/_rels/document.xml.rels')


def test_glued_paragraph_preserves_subscript_runs(tmp_path):
    blocks = p('1．填写水的化学式 H').replace('</w:p>', '<w:r><w:rPr><w:vertAlign w:val="subscript"/></w:rPr><w:t>2</w:t></w:r><w:r><w:t>O。\n2．说明氧气的化学性质。</w:t></w:r></w:p>')
    qs = b.questions_from_docx(document(tmp_path, blocks))
    assert len(qs) == 2
    assert any(part.get('vert') == 'subscript' for part in qs[0]['segments'][0])


def test_curriculum_does_not_claim_official_weights():
    assert '不是天津官方' in qa.CURRICULUM['basis']
    assert len(qa.CURRICULUM['themes']) == 5


def test_batch_import_keeps_papers_and_manual_edits(tmp_path, monkeypatch):
    source = document(tmp_path, p('1．请根据空气组成说明氮气的作用。'))
    con = b.init_db(); _, _, ids = b.insert_questions(con, 'paper.docx', b.questions_from_docx(source)); con.commit()
    paper, error = b.apply_paper(con, {'name':'教师组卷','ids':ids})
    assert not error
    b.assign_qtype(con, ids[0], {'qtype':'填空题'})
    con.close()
    monkeypatch.setattr(b, 'MIRROR', str(tmp_path))
    monkeypatch.setattr(b, 'list_candidates', lambda: [str(source)])
    b.import_papers(20)
    con = b.open_db()
    assert b.get_paper(con, paper['id'])['ids'] == ids
    assert con.execute('SELECT qtype FROM questions WHERE id=?',(ids[0],)).fetchone()[0] == '填空题'
    con.close()


def test_underlined_unicode_spaces_are_answer_blanks(tmp_path):
    blocks = p('1．写出仪器的名称').replace('</w:p>', '<w:r><w:rPr><w:u w:val="single"/></w:rPr><w:t xml:space="preserve">\u00a0\u00a0\u2004\u3000</w:t></w:r><w:r><w:t>。</w:t></w:r></w:p>')
    qs = b.questions_from_docx(document(tmp_path, blocks))
    assert '名称_____。' in qs[0]['body']


def test_legacy_spaced_blanks_export_as_continuous_underlines(tmp_path):
    qs = b.questions_from_docx(document(tmp_path, p('1．写出仪器的名称_\u00a0_\u00a0_，解释原理____。')))
    # The historical bank can split a blank into multiple Word runs.
    qs[0]['segments'] = [[{'t':'text','s':'写出仪器的名称_'}, {'t':'text','s':'\u00a0_\u00a0_，解释原理____。'}]]
    con = b.init_db(); _, _, ids = b.insert_questions(con, '中文试卷.docx', qs); con.commit(); con.close()
    out = tmp_path/'underline.docx'; b.export_docx(ids, str(out))
    with zipfile.ZipFile(out) as archive:
        root = ET.fromstring(archive.read('word/document.xml'))
    underlined = [r for r in root.iter(b.W+'r') if r.find(b.W+'rPr/'+b.W+'u') is not None]
    assert [''.join(n.text or '' for n in r.iter(b.W+'t')) for r in underlined] == ['\u00a0'*5, '\u00a0'*4]
    assert '_' not in ''.join(n.text or '' for n in root.iter(b.W+'t'))


def test_mangled_source_uses_title_without_renaming_file(tmp_path):
    import document_display as display
    source = document(tmp_path, p('期末模拟题三') + p('一、单选题') + p('1．说明空气的主要成分。'))
    mangled = tmp_path/'μ￡_μ_íμ_____(2).docx'; source.rename(mangled)
    original_bytes = mangled.read_bytes()
    result = display.source_info(mangled.name, '22', [tmp_path])
    assert result['label'] == '文档标题：期末模拟题三（原文件名编码异常；原题号 22）'
    assert result['rel_path'] == mangled.name
    assert mangled.read_bytes() == original_bytes


def test_mangled_source_without_title_is_explicit(tmp_path):
    import document_display as display
    source = document(tmp_path, p('1．请说明化学试题中的燃烧现象。'))
    mangled = tmp_path/'μ￡_μ.docx'; source.rename(mangled)
    result = display.source_info(mangled.name, '1', [tmp_path])
    assert result['document_title'] == ''
    assert result['label'] == '未命名来源（原文件名编码异常；原题号 1）'
    assert b.source_label('imports/天津市九年级化学试卷.docx', '22') == 'imports/天津市九年级化学试卷.docx（原题号 22）'
