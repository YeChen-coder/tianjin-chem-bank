"""Keep the original bank UI and integer IDs; structured content is authoritative."""
from copy import deepcopy
import json
import uuid
import banklib
from .storage import ContentError, nodes
from .validation import validate_bundle


class BankBridge:
    def __init__(self, api):
        self.api=api;self.store=api.store;self.workflow=api.workflow
        with self.workflow.legacy() as con:
            con.execute('CREATE TABLE IF NOT EXISTS structured_papers (content_id TEXT PRIMARY KEY, legacy_id INTEGER NOT NULL)')

    def synchronize(self):
        """Register imported papers once. Never reapply saved selections on startup."""
        with self.store.connect() as con:
            linked={row['question_id']:row['legacy_id'] for row in con.execute('SELECT question_id,legacy_id FROM legacy_links')}
        with self.workflow.legacy() as con:
            sourced={row[0] for row in con.execute('SELECT DISTINCT question_id FROM sources')}
        for item in self.workflow.catalog()['papers']:
            pid=item['id'];bundle=self.store.snapshot(pid)
            self.workflow.link_legacy(bundle)
            for q in bundle['questions']:
                lid=linked.get(q['id']) or self.workflow._legacy_id(q['id'])
                if lid not in sourced:
                    with self.workflow.legacy() as con:
                        con.execute('INSERT OR IGNORE INTO sources(question_id,rel_path,orig_qnum) VALUES (?,?,?)',(lid,item['title'],q['number']))
                    sourced.add(lid)
            if not item.get('create_saved_paper',True):continue
            with self.workflow.legacy() as con:
                if con.execute('SELECT 1 FROM structured_papers WHERE content_id=?',(pid,)).fetchone():continue
                ids=[self.workflow._legacy_id(qid) for qid in bundle['paper']['question_ids']]
                paper,error=banklib.apply_paper(con,{'name':item['title'],'ids':ids})
                if error:raise ContentError('PAPER_LINK',error)
                banklib._write_paper_order(con,paper['id'],ids)
                con.execute('INSERT INTO structured_papers VALUES (?,?)',(pid,paper['id']))
        # Include selected AI versions and retained mothers outside source-paper
        # references, without changing saved selections or their ordering.
        with self.store.connect() as con:
            content=[(row['legacy_id'],json.loads(row['json'])) for row in con.execute('SELECT l.legacy_id,q.json FROM legacy_links l JOIN questions q ON l.question_id=q.id')]
        with self.workflow.legacy() as con:
            cached={row['id']:dict(row) for row in con.execute('SELECT q.id,q.major,q.minor,q.category_manual,m.payload FROM questions q LEFT JOIN question_metadata m ON q.id=m.question_id')}
        for lid,q in content:
            row=cached.get(lid)
            metadata=json.loads(row['payload']) if row and row['payload'] else {}
            suggested=metadata.get('categories') or []
            primary=(suggested[0]['major'],suggested[0]['minor']) if suggested else ('未分类','未分类')
            fresh=(row and metadata.get('classification_version')=='legacy-taxonomy-v1'
                   and metadata.get('source_summary_revision',0)==q['revision']
                   and (row['category_manual'] or (row['major'],row['minor'])==primary))
            if not fresh:self.sync_summary(q['id'])

    def paper_link(self,pid):
        with self.workflow.legacy() as con:
            row=con.execute('SELECT legacy_id FROM structured_papers WHERE content_id=?',(pid,)).fetchone()
            paper=banklib.get_paper(con,row[0]) if row else None
        if not paper:raise ContentError('NOT_FOUND','试卷已删除，可从题库重新组卷',404)
        return paper

    def open_imported_paper(self,pid):
        """An explicit upload may open a bulk source as a saved paper once."""
        with self.workflow.legacy() as con:
            if con.execute('SELECT 1 FROM structured_papers WHERE content_id=?',(pid,)).fetchone():
                return self.paper_link(pid)
            bundle=self.store.snapshot(pid)
            ids=[self.workflow._legacy_id(qid) for qid in bundle['paper']['question_ids']]
            paper,error=banklib.apply_paper(con,{'name':bundle['paper']['title'],'ids':ids})
            if error:raise ContentError('PAPER_LINK',error)
            banklib._write_paper_order(con,paper['id'],ids)
            con.execute('INSERT INTO structured_papers VALUES (?,?)',(pid,paper['id']))
        return self.paper_link(pid)

    def question_id(self,lid):
        with self.store.connect() as con:
            row=con.execute('SELECT question_id FROM legacy_links WHERE legacy_id=?',(int(lid),)).fetchone()
        if not row:raise ContentError('NOT_FOUND','此题尚无结构化内容',404)
        return row[0]

    def ensure_handwritten(self,row):
        from .legacy import question_from_legacy
        qid='legacy-'+str(row['id'])
        converted=question_from_legacy(row,self.store)
        q=converted['question']
        if q['issues']:raise ContentError('LEGACY_INVALID','此题含未适配内容，请通过 Word 重新导入')
        pid='handwritten-'+str(row['id'])
        self.store.put_bundle({'schema_version':1,'paper':{'id':pid,'title':'手写题','question_ids':[qid],'source_sha256':None},'questions':[q],'assets':converted['assets'],'issues':q['issues']})
        return qid

    def context(self,lid):
        qid=self.question_id(lid)
        with self.store.connect() as con:
            candidate=con.execute('SELECT paper_id,source_id FROM workflow_candidates WHERE id=?',(qid,)).fetchone()
            if candidate:pid=candidate['paper_id']
            else:
                pid=next((r['id'] for r in con.execute('SELECT id,json FROM papers') if qid in json.loads(r['json'])['paper']['question_ids']),None)
                if not pid:
                    mother=con.execute('SELECT paper_id FROM workflow_candidates WHERE source_id=? LIMIT 1',(qid,)).fetchone()
                    if mother:pid=mother[0]
        if not pid:raise ContentError('NOT_FOUND','题目来源试卷不存在',404)
        return {'paper_id':pid,'question_id':qid}

    def annotate(self,items):
        for item in items:
            try:qid=self.question_id(item['id'])
            except ContentError:
                # This is the private V2 bank, never the formal old bank.
                with self.workflow.legacy() as con:row=con.execute('SELECT * FROM questions WHERE id=?',(item['id'],)).fetchone()
                if not row:continue
                qid=self.ensure_handwritten(dict(row))
            content=self.store.get_question(qid)
            item['structured']=content;item['content_context']=self.context(item['id'])
            item['qtype']=content['question']['qtype']
            if not item['sources']:
                q=content['question'];sha=q['source'].get('source_sha256')
                title=next((p['title'] for p in self.workflow.catalog()['papers'] if p['source_sha256']==sha),'AI 改写' if q['origin']=='ai' else '手写题目')
                item['sources']=[title+'（原题号 '+q['number']+'）']
        return items

    def sync_summary(self,qid):
        q=self.store.get_question(qid)['question']
        try:lid=self.workflow._legacy_id(qid)
        except ContentError:return
        from .bank_metadata import sync_metadata
        with self.workflow.legacy() as con:
            sync_metadata(con,lid,q)

    def set_qtype(self,lid,value):
        if value not in banklib.QTYPES:raise ContentError('BAD_REQUEST','题型无效',400)
        qid=self.question_id(lid);q=self.store.get_question(qid)['question'];q['qtype']=value
        self.store.save_question(qid,q['revision'],q);self.sync_summary(qid)
        with self.workflow.legacy() as con:con.execute('UPDATE questions SET qtype_manual=1 WHERE id=?',(lid,))
        return {'id':lid,'qtype':value}

    def selection(self,payload):
        ids=payload.get('ids');expected=payload.get('expected_revisions')
        if not isinstance(ids,list) or not ids or len(ids)>500 or any(type(i) is not int for i in ids):raise ContentError('BAD_REQUEST','请选择 1 至 500 道题',400)
        if not isinstance(expected,dict):raise ContentError('BAD_REQUEST','导出需要保存版本',400)
        ids=list(dict.fromkeys(ids));questions=[self.store.get_question(self.question_id(i))['question'] for i in ids]
        if expected!={q['id']:q['revision'] for q in questions}:raise ContentError('REVISION_CONFLICT','题目已有新版本，请刷新后导出',409)
        bundle={'schema_version':1,'paper':{'id':'selection-'+uuid.uuid4().hex,'title':str(payload.get('title') or '化学试卷')[:160],'question_ids':[q['id'] for q in questions],'source_sha256':None},'questions':questions,'assets':[],'issues':[]}
        with self.store.connect() as con:
            for row in con.execute('SELECT json FROM papers'):
                source=json.loads(row[0])
                if source['paper']['question_ids']==bundle['paper']['question_ids']:
                    bundle['paper']['annotations']=deepcopy(source['paper'].get('annotations',[]));break
            used={n['asset_id'] for n in nodes(bundle) if n.get('type')=='image'};bundle['assets']=self.store._assets(con,used)
        if payload.get('keep_source'):
            for q in questions:
                with self.workflow.legacy() as con:labels=banklib.source_labels(con,self.workflow._legacy_id(q['id']))
                q['body'].append({'id':'export-source-'+uuid.uuid4().hex,'type':'paragraph','inlines':[{'id':'export-source-text-'+uuid.uuid4().hex,'type':'text','text':'来源：'+('；'.join(labels) or 'AI 改写')}],'align':'left'})
        check=validate_bundle(bundle)
        if not check['ok']:raise ContentError('VALIDATION_FAILED','所选题目结构无效',details=check)
        return bundle

    def candidates(self,lid):
        context=self.context(lid);qid=context['question_id'];items=[]
        for row in self.workflow.candidates(context['paper_id'])['items']:
            if row['source_id']!=qid:continue
            row['content']=self.store.get_question(row['id'])
            try:row['legacy_id']=self.workflow._legacy_id(row['id'])
            except ContentError:row['legacy_id']=None
            items.append(row)
        with self.workflow.legacy() as con:
            jobs=[dict(r) for r in con.execute("SELECT j.id,j.status FROM ai_jobs j JOIN content_jobs c ON j.id=c.job_id WHERE c.source_id=? AND j.status IN ('queued','running')",(qid,))]
        return {'items':items,'jobs':jobs,'context':context}

    def select_candidate(self,cid,payload):
        with self.store.connect() as con:row=con.execute('SELECT * FROM workflow_candidates WHERE id=?',(cid,)).fetchone()
        if not row:raise ContentError('NOT_FOUND','候选不存在',404)
        q=self.store.get_question(cid)['question'];source=self.store.get_question(row['source_id'])['question']
        on=payload.get('on');paper_id=payload.get('paper_id')
        if type(on) is not bool or type(paper_id) is not int:raise ContentError('BAD_REQUEST','采纳请求无效',400)
        if on and row['status']=='candidate_rejected':raise ContentError('CONTENT_REJECTED','候选未通过独立核对，不能放入试卷')
        if on and (source['revision']!=row['source_revision'] or payload.get('expected_source_revision')!=source['revision'] or payload.get('expected_revision')!=q['revision']):raise ContentError('REVISION_CONFLICT','母题或候选已经更新，请重新核对',409)
        # Register the full candidate with a new legacy ID; never flatten into segments.
        self.workflow.link_legacy({'questions':[q]});lid=self.workflow._legacy_id(cid)
        with self.workflow.legacy() as con:
            paper=banklib.get_paper(con,paper_id)
            if not paper:raise ContentError('NOT_FOUND','试卷不存在',404)
            ids=paper['ids'][:]
            if on:
                if lid not in ids:
                    mother=self.workflow._legacy_id(row['source_id']);at=ids.index(mother)+1 if mother in ids else len(ids);ids.insert(at,lid)
                con.execute('UPDATE questions SET origin=\'ai\',in_bank=1,base_question_id=? WHERE id=?',(self.workflow._legacy_id(row['source_id']),lid))
            else:ids=[i for i in ids if i!=lid]
            paper,error=banklib.apply_paper(con,{'name':paper['name'],'ids':ids},paper_id)
            if error:raise ContentError('PAPER_LINK',error)
            banklib._write_paper_order(con,paper_id,ids)
            paper=banklib.get_paper(con,paper_id)
        self.sync_summary(cid)
        return {'paper':paper,'question_id':lid}

    def split(self,lid,payload):
        qid=self.question_id(lid);old=self.store.get_question(qid)['question'];at=payload.get('at')
        if payload.get('expected_revision')!=old['revision']:raise ContentError('REVISION_CONFLICT','题目已有新版本，请刷新',409)
        if type(at) is not int or not 0<at<len(old['body']):raise ContentError('BAD_REQUEST','分隔位置无效',400)
        if old['options'] or old['subquestions'] or old['answers'] or old['explanations']:raise ContentError('SPLIT_REVIEW','此题包含选项、小问或答案。请先在编辑器中整理归属，不能自动拆散关联内容。')
        first=deepcopy(old);second=deepcopy(old);first['body']=first['body'][:at];second['body']=second['body'][at:]
        second['id']='teacher-'+uuid.uuid4().hex;second['revision']=0;second['number']='';second['origin']='teacher';second['issues']=[]
        second['source']={**second['source'],'parent_question_id':qid,'parent_revision':old['revision'],'locator':str(old['source'].get('locator') or '')+':split:'+str(at)}
        # Each moved block retains its identity, only in its new question.
        first['issues']=[i for i in first['issues'] if not i.get('node_id') or i['node_id'] in {n['id'] for n in nodes(first['body'])}]
        with self.store.lock,self.store.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            actual=json.loads(con.execute('SELECT json FROM questions WHERE id=?',(qid,)).fetchone()[0])
            if actual['revision']!=old['revision']:raise ContentError('REVISION_CONFLICT','题目已有新版本',409)
            first['revision']+=1;first['origin']='teacher'
            for row in con.execute('SELECT id,json FROM papers').fetchall():
                bundle=json.loads(row['json']);ids=bundle['paper']['question_ids']
                if qid not in ids:continue
                ids.insert(ids.index(qid)+1,second['id'])
                check={**bundle,'questions':[first if i==qid else second if i==second['id'] else json.loads(con.execute('SELECT json FROM questions WHERE id=?',(i,)).fetchone()[0]) for i in ids],'assets':self.store._assets(con)}
                report=validate_bundle(check)
                if not report['ok']:raise ContentError('VALIDATION_FAILED','分隔后内容关联无效',details=report)
                con.execute('UPDATE papers SET json=? WHERE id=?',(json.dumps(bundle,ensure_ascii=False),row['id']))
            con.execute('INSERT INTO history VALUES (?,?,?)',(qid,old['revision'],json.dumps(old,ensure_ascii=False)))
            con.execute('UPDATE questions SET json=? WHERE id=?',(json.dumps(first,ensure_ascii=False),qid))
            con.execute('INSERT INTO questions VALUES (?,?)',(second['id'],json.dumps(second,ensure_ascii=False)))
        self.workflow.link_legacy({'questions':[second]});newid=self.workflow._legacy_id(second['id']);self.sync_summary(qid);self.sync_summary(second['id'])
        with self.workflow.legacy() as con:
            for row in con.execute('SELECT DISTINCT paper_id FROM paper_items WHERE question_id=?',(lid,)).fetchall():
                paper=banklib.get_paper(con,row[0]);ids=paper['ids'];ids.insert(ids.index(lid)+1,newid);banklib._write_paper_order(con,row[0],ids)
            paper=banklib.get_paper(con,payload.get('paper_id')) if payload.get('paper_id') else None
        return {'question_id':lid,'new_id':newid,'paper':paper}
