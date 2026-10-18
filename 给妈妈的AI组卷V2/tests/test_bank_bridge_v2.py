from copy import deepcopy
import hashlib
from types import SimpleNamespace
import unittest
from zipfile import ZipFile
from lxml import etree

import banklib
from question_content.bank_bridge import BankBridge
from question_content.source_layout import restore_source_blanks
from question_content.bank_metadata import question_text
from question_content.storage import ContentError
import test_workflow_integration_v2 as fixtures
NS=fixtures.NS


class BankBridgeTests(unittest.TestCase):
    setUp=fixtures.WorkflowTests.setUp
    tearDown=fixtures.WorkflowTests.tearDown
    candidate_result=fixtures.WorkflowTests.candidate_result

    def bridge(self):
        b=BankBridge(SimpleNamespace(store=self.store,workflow=self.workflow))
        b.synchronize()
        return b

    def test_restart_preserves_teacher_selection_and_source_search(self):
        b=self.bridge();paper=b.paper_link(self.pid);selected=paper['ids'][1:]
        with self.workflow.legacy() as con:
            banklib.apply_paper(con,{'name':'老师组卷','ids':selected},paper['id'])
        b.synchronize()
        self.assertEqual(b.paper_link(self.pid)['ids'],selected)
        with self.workflow.legacy() as con:
            self.assertTrue(con.execute('SELECT 1 FROM sources WHERE question_id=?',(selected[0],)).fetchone())

    def test_candidate_add_remove_preserves_mother_and_order(self):
        b=self.bridge();paper=b.paper_link(self.pid)
        cid=self.workflow.commit_result(self.pid,self.question['id'],0,self.candidate_result())
        result=b.select_candidate(cid,{'on':True,'paper_id':paper['id'],'expected_source_revision':0,'expected_revision':0})
        self.assertEqual(result['paper']['ids'],[paper['ids'][0],result['question_id'],paper['ids'][1]])
        self.assertEqual(self.store.get_question(self.question['id'])['question'],self.question)
        self.assertEqual(b.select_candidate(cid,{'on':False,'paper_id':paper['id']})['paper']['ids'],paper['ids'])

    def test_rejected_and_stale_candidates_cannot_enter_saved_paper(self):
        b=self.bridge();paper=b.paper_link(self.pid)
        cid=self.workflow.commit_result(self.pid,self.question['id'],0,self.candidate_result('candidate_rejected'))
        with self.assertRaises(ContentError):b.select_candidate(cid,{'on':True,'paper_id':paper['id'],'expected_source_revision':0,'expected_revision':0})
        ready=self.candidate_result();ready['candidate']['id']+='-ready'
        other=self.workflow.commit_result(self.pid,self.question['id'],0,ready)
        q=deepcopy(self.question);q['body'][0]['inlines'][0]['text']='教师的新题干'
        self.store.save_question(q['id'],0,q)
        with self.assertRaises(ContentError) as exc:b.select_candidate(other,{'on':True,'paper_id':paper['id'],'expected_source_revision':1,'expected_revision':0})
        self.assertEqual(exc.exception.status,409)
        self.assertEqual(b.paper_link(self.pid)['ids'],paper['ids'])

    def test_export_uses_saved_content_and_rejects_stale_revision(self):
        b=self.bridge();paper=b.paper_link(self.pid)
        q=deepcopy(self.question);q['body'][0]['inlines'][0]['text']='教师保存的版本'
        self.store.save_question(q['id'],0,q);b.sync_summary(q['id'])
        with self.assertRaises(ContentError):b.selection({'ids':[paper['ids'][0]],'expected_revisions':{q['id']:0}})
        snapshot=b.selection({'ids':[paper['ids'][0]],'expected_revisions':{q['id']:1},'keep_source':True})
        self.assertEqual(snapshot['questions'][0]['body'][0]['inlines'][0]['text'],'教师保存的版本')
        self.assertNotIn('word/document.xml',str(snapshot['questions'][0]['body'][-1]))

    def test_retained_mother_context_after_candidate_replacement(self):
        b=self.bridge();lid=self.workflow._legacy_id(self.question['id'])
        cid=self.workflow.commit_result(self.pid,self.question['id'],0,self.candidate_result())
        self.workflow.adopt(self.pid,cid,{'expected_source_revision':0,'expected_candidate_revision':0})
        self.assertEqual(b.context(lid)['paper_id'],self.pid)

    def test_split_keeps_moved_node_identity_and_saved_order(self):
        b=self.bridge();paper=b.paper_link(self.pid);q=deepcopy(self.question)
        q['body'].append({'id':'extra-paragraph','type':'paragraph','align':'left','inlines':[{'id':'extra-text','type':'text','text':'另一道独立题目。'}]})
        self.store.save_question(q['id'],0,q)
        result=b.split(paper['ids'][0],{'at':1,'expected_revision':1,'paper_id':paper['id']})
        self.assertEqual(result['paper']['ids'],[paper['ids'][0],result['new_id'],paper['ids'][1]])
        second=self.store.get_question(b.question_id(result['new_id']))['question']
        self.assertEqual(second['body'][0]['id'],'extra-paragraph')
        self.assertEqual(self.store.get_question(q['id'])['question']['revision'],2)

    def test_underlined_spaces_only_restore_unchanged_source_nodes(self):
        path=self.root/'blank.docx'
        xml=f'<w:document xmlns:w="{NS}"><w:body><w:p><w:r><w:rPr><w:u w:val="single"/></w:rPr><w:t>      </w:t></w:r><w:r><w:t>      </w:t></w:r></w:p></w:body></w:document>'
        with ZipFile(path,'w') as z:z.writestr('word/document.xml',xml)
        tree=etree.fromstring(xml.encode());texts=tree.findall('.//{'+NS+'}t')
        def node(t):return {'id':'text-'+hashlib.sha256(('sha:word/document.xml:'+t.getroottree().getpath(t)).encode()).hexdigest()[:24],'type':'text','text':t.text}
        source=[node(t) for t in texts]
        bundle={'paper':{'source_sha256':'sha'},'questions':[{'body':[{'inlines':source}]}]}
        edited=deepcopy(bundle);edited['questions'][0]['body'][0]['inlines'][0]['text']='老师修改'
        self.assertEqual(restore_source_blanks(edited,path),0)
        legacy_bundle=deepcopy(bundle)
        legacy=self.root/'legacy.doc'
        with ZipFile(legacy,'w') as z:z.writestr('Legacy.xml','not OOXML')
        (self.root/'converted.docx').write_bytes(path.read_bytes())
        self.assertEqual(restore_source_blanks(legacy_bundle,legacy),1)
        self.assertEqual(restore_source_blanks(bundle,path),1)
        self.assertEqual(source[0]['type'],'blank');self.assertEqual(source[1]['type'],'text')

    def test_word_import_runs_original_classifier_before_ui_registration(self):
        from question_import_v2 import import_paper
        from question_content.workflow import ImportServices
        path=self.root/'classification.docx'
        with ZipFile(path,'w') as z:z.writestr('word/document.xml',f'<w:document xmlns:w="{NS}"><w:body><w:p><w:r><w:t>1．根据质量守恒定律填写化学方程式。</w:t></w:r></w:p></w:body></w:document>')
        result=import_paper(path,self.root/'classified',ImportServices(self.workflow))
        pid=self.workflow.add_import(result)['paper_id'];q=self.store.snapshot(pid)['questions'][0]
        lid=self.workflow._legacy_id(q['id'])
        with self.workflow.legacy() as con:
            row=con.execute('SELECT * FROM questions WHERE id=?',(lid,)).fetchone()
            self.assertEqual(row['minor'],'质量守恒定律');self.assertEqual(row['category_manual'],0)
            self.assertIn('质量守恒定律',banklib.load_labels(con,lid,row['major'],row['minor'])[1])
        self.assertEqual(self.store.get_question(q['id'])['question'],q)

    def test_automatic_categories_update_but_teacher_labels_survive(self):
        b=self.bridge();qid=self.question['id'];lid=self.workflow._legacy_id(qid)
        q=deepcopy(self.question);q['body'][0]['inlines'][0]['text']='根据质量守恒定律说明化学方程式。'
        self.store.save_question(qid,0,q);b.sync_summary(qid)
        with self.workflow.legacy() as con:
            row=con.execute('SELECT * FROM questions WHERE id=?',(lid,)).fetchone()
            self.assertEqual(row['minor'],'质量守恒定律')
            _,error=banklib.assign_category(con,lid,{'new_major':'老师单元','new_minor':'老师专题'})
            self.assertIsNone(error)
        changed=self.store.get_question(qid)['question'];changed['body'][0]['inlines'][0]['text']='使用托盘天平和酒精灯进行实验。'
        self.store.save_question(qid,1,changed);b.synchronize()
        with self.workflow.legacy() as con:
            row=con.execute('SELECT * FROM questions WHERE id=?',(lid,)).fetchone()
            self.assertEqual((row['major'],row['minor'],row['category_manual']),('老师单元','老师专题',1))
            self.assertEqual(banklib.load_labels(con,lid,row['major'],row['minor']),(['老师单元'],['老师专题']))

    def test_summary_includes_options_table_and_plain_formula(self):
        q=deepcopy(self.question)
        q['options']=[{'label':'A','blocks':[{'type':'paragraph','inlines':[{'type':'formula','latex':r'\mathrm{C}_{60}'},{'type':'text','text':'与石墨'}]}]},
                      {'label':'B','blocks':[{'type':'table','rows':[{'cells':[{'col_index':0,'blocks':[{'type':'paragraph','inlines':[{'type':'text','text':'托盘天平'}]}]}]}]}]}]
        summary=question_text(q)
        self.assertIn('A. C60与石墨',summary);self.assertIn('B. 托盘天平',summary)
        self.assertNotIn('mathrm',summary)

    def test_summary_handles_leaf_subquestion_without_optional_children(self):
        q=deepcopy(self.question)
        q['subquestions']=[{'label':'(1)','blocks':[{'type':'paragraph','inlines':[{'type':'text','text':'解释质量守恒定律。'}]}]}]
        self.assertIn('(1) 解释质量守恒定律。',question_text(q))

    def test_startup_classifies_selected_ai_without_reordering_teacher_paper(self):
        b=self.bridge();paper=b.paper_link(self.pid)
        result=self.candidate_result();result['candidate']['body'][0]['inlines'][0]['text']='托盘天平、酒精灯：实验操作正确的是？'
        cid=self.workflow.commit_result(self.pid,self.question['id'],0,result)
        added=b.select_candidate(cid,{'on':True,'paper_id':paper['id'],'expected_source_revision':0,'expected_revision':0})
        with self.workflow.legacy() as con:
            con.execute("UPDATE questions SET major='未分类',minor='未分类' WHERE id=?",(added['question_id'],))
            banklib.set_question_labels(con,added['question_id'],['未分类'],['未分类'])
        b.synchronize()
        self.assertEqual(b.paper_link(self.pid)['ids'],added['paper']['ids'])
        with self.workflow.legacy() as con:
            self.assertEqual(con.execute('SELECT minor FROM questions WHERE id=?',(added['question_id'],)).fetchone()[0],'走进化学实验室')

    def test_bulk_sources_are_searchable_without_creating_saved_papers(self):
        from question_import_v2 import import_paper
        from question_content.workflow import ImportServices
        b=self.bridge()
        path=self.root/'bulk.docx'
        with ZipFile(path,'w') as z:z.writestr('word/document.xml',f'<w:document xmlns:w="{NS}"><w:body><w:p><w:r><w:t>1．下列实验操作正确的是？</w:t></w:r></w:p></w:body></w:document>')
        result=import_paper(path,self.root/'bulk-parsed',ImportServices(self.workflow))
        pid=self.workflow.add_import(result,'批量来源',create_saved_paper=False)['paper_id']
        b.synchronize()
        lid=self.workflow._legacy_id(self.store.snapshot(pid)['questions'][0]['id'])
        with self.workflow.legacy() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM papers').fetchone()[0],1)
            self.assertTrue(con.execute('SELECT 1 FROM sources WHERE question_id=?',(lid,)).fetchone())
        opened=b.open_imported_paper(pid)
        self.assertEqual(b.open_imported_paper(pid)['id'],opened['id'])
        with self.workflow.legacy() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM papers').fetchone()[0],2)

    def test_resume_repairs_missing_link_without_duplicating_legacy_question(self):
        qid=self.question['id'];lid=self.workflow._legacy_id(qid)
        with self.store.connect() as con:con.execute('DELETE FROM legacy_links WHERE question_id=?',(qid,))
        import base64
        response=self.workflow.import_upload('paper.docx',base64.b64encode((self.root/'paper.docx').read_bytes()).decode())
        self.assertTrue(response['duplicate']);self.assertEqual(self.workflow._legacy_id(qid),lid)
        with self.workflow.legacy() as con:self.assertEqual(con.execute('SELECT COUNT(*) FROM questions').fetchone()[0],2)

    def test_batch_source_conflict_rolls_back_all_new_records(self):
        self.store.register_source('fixed',{'value':1})
        with self.assertRaises(ContentError):self.store.register_sources({'new':{'value':2},'fixed':{'value':3}})
        self.assertIsNone(self.store.resolve_source('new'))
        self.assertEqual(self.store.resolve_source('fixed'),{'value':1})


if __name__=='__main__':unittest.main()
