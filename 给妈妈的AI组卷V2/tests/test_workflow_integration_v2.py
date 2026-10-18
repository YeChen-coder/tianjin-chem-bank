import base64
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

import banklib
import aivariant
from question_content import compile_formula,validate_bundle
from question_content.storage import ContentStore,ContentError,nodes
from question_content.workflow import Workflow,ImportServices
from question_import_v2 import import_paper
from word_export_v2 import export_paper
from question_content.http_api import AssetResolver

ROOT=Path(__file__).resolve().parents[1]
NS='http://schemas.openxmlformats.org/wordprocessingml/2006/main'


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        base=ROOT/'.work/session-4/integration/tests';base.mkdir(parents=True,exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=base);self.root=Path(self.tmp.name)
        self.patch=patch.multiple(banklib,DATA_DIR=str(self.root),DB_PATH=str(self.root/'legacy.sqlite'))
        self.patch.start();con=banklib.init_db();con.close();self.store=ContentStore(self.root/'store')
        self.workflow=Workflow(self.store,ROOT,banklib.DB_PATH,client_factory=lambda:None)
        source=self.root/'paper.docx'
        with ZipFile(source,'w') as z:z.writestr('word/document.xml',f'<w:document xmlns:w="{NS}"><w:body><w:p><w:r><w:t>相对原子质量：H-1 O-16</w:t></w:r></w:p><w:p><w:r><w:t>1．写出水的化学式。</w:t></w:r></w:p><w:p><w:r><w:t>2．计算水的相对分子质量。</w:t></w:r></w:p></w:body></w:document>')
        result=import_paper(source,self.root/'parsed',ImportServices(self.workflow))
        self.pid=self.workflow.add_import(result)['paper_id'];self.question=self.store.snapshot(self.pid)['questions'][0]
    def tearDown(self):self.patch.stop();self.tmp.cleanup();aivariant.CONTENT_JOB_HANDLER=None
    def candidate_result(self,status='candidate_ready'):
        q=deepcopy(self.question)
        def reid(value):
            if isinstance(value,dict):
                if 'id' in value:value['id']='candidate-'+value['id']
                for v in value.values():reid(v)
            elif isinstance(value,list):
                for v in value:reid(v)
        reid(q);q['origin']='ai';q['source']={'parent_question_id':self.question['id'],'parent_revision':0}
        return {'candidate':q,'status':status,'ok':status!='candidate_rejected','judgement':{'status':'FAIL' if status=='candidate_rejected' else 'PASS'},'source_id':self.question['id']}
    def test_import_preserves_original_annotations(self):
        b=self.store.snapshot(self.pid);self.assertTrue(validate_bundle(b)['ok'])
        self.assertTrue(b['paper']['annotations']);self.assertIn('H-1',json.dumps(b,ensure_ascii=False))
        result=export_paper(b,self.root/'out.docx',{},AssetResolver(self.store))
        with ZipFile(result['output_path']) as z:self.assertIn('相对原子质量',z.read('word/document.xml').decode())
    def test_reimport_does_not_overwrite_teacher_save(self):
        q=deepcopy(self.question);q['body'][0]['inlines'][0]['text']='老师修改';self.store.save_question(q['id'],0,q)
        payload=base64.b64encode((self.root/'paper.docx').read_bytes()).decode()
        response=self.workflow.import_upload('再次导入.docx',payload)
        self.assertTrue(response['duplicate']);self.assertEqual(self.store.get_question(q['id'])['question']['revision'],1)
    def test_candidate_adopt_restore_preserves_original(self):
        r=self.candidate_result();cid=self.workflow.commit_result(self.pid,self.question['id'],0,r)
        self.workflow.adopt(self.pid,cid,{'expected_source_revision':0,'expected_candidate_revision':0})
        self.assertIn(cid,self.store.snapshot(self.pid)['paper']['question_ids'])
        self.assertEqual(self.store.get_question(self.question['id'])['question'],self.question)
        self.workflow.restore(self.pid,cid,{'expected_candidate_revision':0})
        self.assertIn(self.question['id'],self.store.snapshot(self.pid)['paper']['question_ids'])
    def test_rejected_candidate_cannot_enter_paper(self):
        r=self.candidate_result('candidate_rejected');cid=self.workflow.commit_result(self.pid,self.question['id'],0,r)
        with self.assertRaises(ContentError):self.workflow.adopt(self.pid,cid,{'expected_source_revision':0,'expected_candidate_revision':0})
        self.assertNotIn(cid,self.store.snapshot(self.pid)['paper']['question_ids'])
    def test_stale_response_is_not_saved(self):
        q=deepcopy(self.question);q['body'][0]['inlines'][0]['text']='新版本';self.store.save_question(q['id'],0,q)
        self.assertIsNone(self.workflow.commit_result(self.pid,q['id'],0,self.candidate_result()))
        self.assertEqual(self.workflow.candidates(self.pid)['items'],[])
    def test_cancelled_original_job_discards_late_response(self):
        with patch.object(aivariant,'start_worker'):
            job=self.workflow.enqueue(self.pid,self.question['id'],{'expected_revision':0})
        self.workflow.cancel(job['job_id'])
        self.workflow.commit_result(self.pid,self.question['id'],0,self.candidate_result(),job['version_id'],job['job_id'])
        self.assertEqual(self.workflow.job(job['job_id'])['status'],'cancelled')
        self.assertEqual(self.workflow.candidates(self.pid)['items'],[])
    def test_changed_mother_blocks_old_candidate_adoption(self):
        r=self.candidate_result();cid=self.workflow.commit_result(self.pid,self.question['id'],0,r)
        q=deepcopy(self.question);q['body'][0]['inlines'][0]['text']='新版本';self.store.save_question(q['id'],0,q)
        with self.assertRaises(ContentError) as exc:self.workflow.adopt(self.pid,cid,{'expected_source_revision':1,'expected_candidate_revision':0})
        self.assertEqual(exc.exception.status,409)
    def test_legal_slash_unit_preserved(self):
        for latex in [r'\mathrm{g/L}',r'm(\mathrm{CO}_{2})=0.448\ \mathrm{L}\times1.98\ \mathrm{g/L}\approx0.89\ \mathrm{g}']:
            result=compile_formula(latex);self.assertTrue(result['ok'],result)


if __name__=='__main__':unittest.main()
