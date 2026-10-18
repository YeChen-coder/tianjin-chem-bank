from copy import deepcopy
import unittest
from question_content.import_selection import filename_reason,select_questions


class ImportSelectionTests(unittest.TestCase):
    def question(self,text,options=None):
        return {'id':'q','number':'1','body':[{'id':'p','type':'paragraph','inlines':[{'id':'t','type':'text','text':text}]}], 'options':options or [],'subquestions':[]}
    def result(self,q):
        return {'bundle':{'paper':{'question_ids':['q']},'questions':[q],'issues':[]},'report':{'question_count':1}}
    def test_notes_and_other_subjects_are_excluded_but_exercises_remain(self):
        for name in ['化学必备知识点.docx','化学答题纸.docx','教学计划.docx','第14课诗词三首练习题.docx']:
            self.assertIsNotNone(filename_reason(name))
        for name in ['化学方程式书写专项练习.docx','二氧化碳练习题.docx','天津中考化学试卷.docx']:
            self.assertIsNone(filename_reason(name))
    def test_numbered_knowledge_item_is_not_a_question(self):
        result=self.result(self.question('氧气具有助燃性，氢气具有可燃性。'))
        selected,removed=select_questions(result)
        self.assertEqual(selected['bundle']['questions'],[]);self.assertEqual(len(removed),1)
        self.assertEqual(len(result['bundle']['questions']),1)
    def test_options_and_actual_asks_are_preserved(self):
        for q in [self.question('请计算该溶液的质量分数。'),self.question('化学与生活。',[{'label':'A','blocks':[]},{'label':'B','blocks':[]}]),self.question('该物质的化学式是_______。')]:
            selected,removed=select_questions(self.result(q));self.assertEqual(selected['bundle']['questions'],[q]);self.assertEqual(removed,[])

    def test_image_ocr_is_searchable_but_teacher_text_takes_precedence(self):
        from question_content.bank_metadata import question_text
        q=self.question('');q['body']=[{'id':'image','type':'image','asset_id':'asset'}];q['source']={'ocr_search_text':'下列溶液的说法正确的是？'}
        self.assertIn('溶液',question_text(q))
        q['body'].append({'id':'p','type':'paragraph','inlines':[{'type':'text','text':'老师重新写的题干'}]})
        self.assertNotIn('溶液',question_text(q));self.assertIn('老师重新写',question_text(q))

    def test_png_zip_checksum_repair_preserves_source_and_media_bytes(self):
        from pathlib import Path
        from zipfile import ZipFile,BadZipFile
        import tempfile,io,hashlib,struct
        from PIL import Image
        from question_content.archive_repair import repair_png_crc
        root=Path(__file__).resolve().parents[1]/'.work/session-4/bulk-import/tests';root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as directory:
            source=Path(directory)/'bad.docx';out=Path(directory)/'repaired.docx'
            png=io.BytesIO();Image.new('RGB',(10,10),'white').save(png,format='PNG');data=png.getvalue()
            with ZipFile(source,'w') as z:z.writestr('word/media/image1.png',data)
            damaged=bytearray(source.read_bytes());central=damaged.index(b'PK\x01\x02')
            struct.pack_into('<I',damaged,central+16,0);struct.pack_into('<I',damaged,14,0);source.write_bytes(damaged)
            before=hashlib.sha256(source.read_bytes()).hexdigest()
            with ZipFile(source) as z:
                with self.assertRaises(BadZipFile):z.read('word/media/image1.png')
            report=repair_png_crc(source,out)
            with ZipFile(out) as z:self.assertEqual(z.read('word/media/image1.png'),data)
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),before)
            self.assertFalse(report['media_pixels_changed'])


if __name__=='__main__':unittest.main()
