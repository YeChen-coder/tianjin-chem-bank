"""Regression for existing classification, teacher edits, references and export."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile
import banklib as b

ROOT=Path(__file__).resolve().parents[1]


class LegacyRegression(unittest.TestCase):
    def setUp(self):
        base=ROOT/'.work/session-4/legacy-tests';base.mkdir(parents=True,exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=base);self.root=Path(self.tmp.name)
        self.patch=patch.multiple(b,DATA_DIR=str(self.root),DB_PATH=str(self.root/'bank.sqlite'),MEDIA=str(self.root/'media'),MEDIA_ORIG=str(self.root/'media/original'),IMPORT_DIR=str(self.root/'imports'))
        self.patch.start();self.con=b.init_db();self.con.row_factory=__import__('sqlite3').Row
        self.questions=[{'qnum':str(i+1),'body':text,'answer':answer,'segments':[[{'t':'text','s':text}]],'images':[],'qtype':'填空题'} for i,(text,answer) in enumerate([('请根据质量守恒定律填写化学方程式。','质量守恒'),('说明氮气在空气中的作用。','保护气')])]
        _,_,self.ids=b.insert_questions(self.con,'fixture-source.docx',self.questions);self.con.commit()
    def tearDown(self):self.con.close();self.patch.stop();self.tmp.cleanup()
    def test_reimport_retains_teacher_edits_and_classification(self):
        qid=self.ids[0]
        _,error=b.assign_category(self.con,qid,{'new_major':'老师分类','new_minor':'老师小类'});self.assertIsNone(error)
        _,error=b.assign_qtype(self.con,qid,{'qtype':'实验题'});self.assertIsNone(error)
        _,error=b.assign_body(self.con,qid,{'body':'老师改过的题干'});self.assertIsNone(error)
        _,merged,ids=b.insert_questions(self.con,'second-source.docx',self.questions);self.con.commit()
        self.assertEqual(ids,self.ids);self.assertEqual(merged,2)
        row=self.con.execute('SELECT * FROM questions WHERE id=?',(qid,)).fetchone()
        self.assertEqual(row['body'],'老师改过的题干');self.assertEqual(row['qtype'],'实验题');self.assertEqual(row['major'],'老师分类')
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM sources WHERE question_id=?',(qid,)).fetchone()[0],2)
    def test_paper_reference_and_order_persist(self):
        paper,error=b.apply_paper(self.con,{'name':'教师组卷','ids':self.ids});self.assertIsNone(error)
        moved,error=b.move_paper_question(self.con,paper['id'],self.ids[1],'up');self.assertIsNone(error)
        self.assertEqual(moved['ids'],self.ids[::-1])
        b.insert_questions(self.con,'repeat.docx',self.questions);self.con.commit()
        self.assertEqual(b.get_paper(self.con,paper['id'])['ids'],self.ids[::-1])
    def test_legacy_answer_export_preserves_order(self):
        out=self.root/'legacy.docx';b.export_docx(self.ids[::-1],str(out),answers_at_end=True)
        with ZipFile(out) as z:xml=z.read('word/document.xml').decode()
        self.assertLess(xml.index('保护气'),xml.index('质量守恒</w:t>'))
        self.assertIn('参考答案',xml)


if __name__=='__main__':unittest.main()
