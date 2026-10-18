"""Integration owner: real import, content library, legacy metadata and AI jobs."""
import base64
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import threading
import uuid
from zipfile import ZipFile

from . import compile_formula,validate_bundle,validate_question
from .storage import ContentError,nodes


def encoded(value):return json.dumps(value,ensure_ascii=False)
def digest(value):return hashlib.sha256(encoded(value).encode()).hexdigest()
def load(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))


class Workflow:
    def __init__(self,store,root,legacy_path=None,client_factory=None):
        import banklib
        self.store=store;self.root=Path(root).resolve();self.legacy_path=Path(legacy_path or banklib.DB_PATH).resolve()
        if not self.legacy_path.is_relative_to(self.root/'.work'):raise ContentError('WORKSPACE','集成只使用 V2 私有数据库')
        self.client_factory=client_factory;self.import_lock=threading.Lock()
        with store.connect() as con:
            con.executescript('''
                CREATE TABLE IF NOT EXISTS workflow_papers (id TEXT PRIMARY KEY, metadata TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS workflow_candidates (id TEXT PRIMARY KEY, paper_id TEXT, source_id TEXT, source_revision INTEGER, status TEXT, result TEXT, version_id INTEGER, adopted INTEGER DEFAULT 0);
            ''')
        with self.legacy() as con:
            con.execute('CREATE TABLE IF NOT EXISTS content_jobs (job_id INTEGER PRIMARY KEY, paper_id TEXT, source_id TEXT, revision INTEGER, snapshot TEXT, assets TEXT, result TEXT)')
        # Existing dispatcher, queue, cancellation and concurrency limits are retained.
        import aivariant
        aivariant.CONTENT_JOB_HANDLER=self.dispatch

    @contextmanager
    def legacy(self):
        con=sqlite3.connect(self.legacy_path,timeout=30);con.row_factory=sqlite3.Row
        try:
            with con:yield con
        finally:con.close()

    def catalog(self):
        with self.store.connect() as con:
            papers=[]
            for row in con.execute('SELECT p.json,w.metadata FROM papers p JOIN workflow_papers w ON p.id=w.id ORDER BY w.rowid'):
                b=load_json(row['json']);meta=load_json(row['metadata'])
                papers.append({'id':b['paper']['id'],'title':b['paper']['title'],'question_count':len(b['paper']['question_ids']),'source_sha256':b['paper']['source_sha256'],'label':meta.get('label'), 'create_saved_paper':meta.get('create_saved_paper',True), 'import_report':meta['public_report']})
        return {'papers':papers,'ai_configured':self.ai_configured()}

    def ai_configured(self):
        if self.client_factory:return True
        import os
        from ai_formula_v2.glm_transport import read_glm_config
        config=read_glm_config(os.environ.get('CHEM_GLM_CONFIG') or None)
        return bool(config.get('GLM_API_KEY')) and config.get('CHEM_DISABLE_AI')!='1'

    def add_import(self,result,label=None,*,create_saved_paper=True):
        bundle=deepcopy(result['bundle']);report=result['report'];pid=bundle['paper']['id']
        # Only trusted Python import results enter this method, never browser file paths.
        base=Path(report['original_copy_path']).parent.resolve()
        if not base.is_relative_to(self.root/'.work'):raise ContentError('IMPORT_PATH','导入结果必须来自 V2 私有目录')
        manifest=load(report['asset_manifest_path'])
        for aid,record in manifest.items():
            original=Path(record['local_path']).resolve();preview=Path(record['preview_path']).resolve() if record.get('preview_path') else None
            if not original.is_relative_to(base) or (preview and not preview.is_relative_to(base)):raise ContentError('IMPORT_PATH','资产清单越过导入目录')
            registered=self.store.register_asset(original,preview)
            if registered!=record['asset'] or registered['id']!=aid:raise ContentError('ASSET_HASH','导入资产身份不一致')
        if report.get('source_records_path'):
            path=Path(report['source_records_path']).resolve()
            if not path.is_relative_to(base):raise ContentError('IMPORT_PATH','来源清单越界')
            self.store.register_sources(load(path))
        if report.get('retained_content_path') and report.get('ordered_document_path'):
            retained=load(report['retained_content_path']);ordered=load(report['ordered_document_path']);boundaries=load(report['boundary_report_path'])
            positions={b['id']:i for i,b in enumerate(ordered)}
            anchors=sorted((positions[b['source_block_id']],b['question_id']) for b in boundaries if b.get('source_block_id') in positions and b['question_id'] in bundle['paper']['question_ids'])
            annotations=[]
            for item in retained:
                block=item['block'];position=positions.get(block['id'],-1)
                before=next((qid for pos,qid in anchors if pos>position),None)
                annotations.append({'before_question_id':before,'blocks':[block]})
            bundle['paper']['annotations']=annotations
        if label:bundle['paper']['title']=label
        from .source_layout import restore_source_blanks
        restore_source_blanks(bundle,report['original_copy_path'])
        validation=validate_bundle(bundle)
        if not validation['ok']:raise ContentError('VALIDATION_FAILED','导入整卷验证失败',details=validation)
        duplicate=False
        try:self.store.put_bundle(bundle)
        except ContentError as exc:
            if exc.code!='PAPER_EXISTS':raise
            duplicate=True;bundle=self.store.snapshot(pid)
        public={'question_count':len(bundle['questions']),'formula_count':report.get('formula_count',0),'image_count':report.get('image_count',0),'issue_count':len(bundle['issues']),'source_sha256':bundle['paper']['source_sha256'],'user_confirmation':'pending','status':report['status']}
        metadata={'label':label,'public_report':public,'create_saved_paper':create_saved_paper,'original_copy_path':report['original_copy_path'],'import_result_path':str(base/'import-result.json')}
        with self.store.connect() as con:con.execute('INSERT OR IGNORE INTO workflow_papers VALUES (?,?)',(pid,encoded(metadata)))
        self.link_legacy(bundle)
        return {'paper_id':pid,'duplicate':duplicate,'report':self.paper_info(pid)['import_report']}

    def link_legacy(self,bundle):
        import banklib
        for q in bundle['questions']:
            with self.store.connect() as con:
                if con.execute('SELECT 1 FROM legacy_links WHERE question_id=?',(q['id'],)).fetchone():continue
            body=''.join(n.get('text','') or n.get('latex','') or '' for n in nodes(q['body']) if n.get('type') in {'text','formula'})
            with self.legacy() as con:
                con.execute('BEGIN IMMEDIATE')
                previous=con.execute('SELECT id FROM questions WHERE dedup_key=?',('v2:'+q['id'],)).fetchone()
                if previous:
                    lid=previous[0]
                    self.store.link_legacy(q['id'],lid,{'schema_version':1,'source':q['source'],'summary_only':True})
                    continue
                # Derived search summary only; structured content stays authoritative.
                cur=con.execute('''INSERT INTO questions (dedup_key,qnum,body,answer,segments,major,minor,image_count,qtype,origin)
                    VALUES (?,?,?,?,?,?,?,?,?,?)''',('v2:'+q['id'],q['number'],body,'','[]','未分类','未分类',sum(n.get('type')=='image' for n in nodes(q)),q['qtype'],'human'))
                lid=cur.lastrowid
                con.execute('INSERT INTO question_metadata(question_id,payload,needs_review) VALUES (?,?,?)',(lid,encoded({'structured_question_id':q['id'],'source':q['source']}),1))
                from .bank_metadata import sync_metadata
                sync_metadata(con,lid,q)
            self.store.link_legacy(q['id'],lid,{'schema_version':1,'source':q['source'],'summary_only':True})

    def import_upload(self,name,content):
        if not isinstance(name,str) or Path(name).suffix.lower() not in {'.docx','.doc'}:raise ContentError('BAD_REQUEST','请选择 Word 的 DOCX 或 DOC 文件',400)
        try:data=base64.b64decode(content,validate=True)
        except (ValueError,TypeError):raise ContentError('BAD_REQUEST','上传文件编码无效',400)
        if not data or len(data)>25*1024*1024:raise ContentError('BAD_REQUEST','Word 文件为空或超过 25 MB',400)
        work=self.store.workspace/'imports'/uuid.uuid4().hex;work.mkdir(parents=True)
        path=work/('upload'+Path(name).suffix.lower());path.write_bytes(data)
        from question_import_v2 import import_paper
        services=ImportServices(self)
        with self.import_lock:result=import_paper(path,work/'parsed',services)
        if not result['bundle']['questions']:raise ContentError('IMPORT_FAILED','未识别到可编辑题目，原文件已保留',details={'status':result['report']['status']})
        title=Path(name.replace('\\','/')).stem[:160]
        return self.add_import(result,title)

    def paper_info(self,pid):
        with self.store.connect() as con:
            row=con.execute('SELECT metadata FROM workflow_papers WHERE id=?',(pid,)).fetchone()
        if not row:raise ContentError('NOT_FOUND','整卷不存在',404)
        meta=load_json(row[0]);return {'import_report':meta['public_report'],'original_url':'/api/v2/content/papers/'+pid+'/original'}

    def original(self,pid):
        with self.store.connect() as con:row=con.execute('SELECT metadata FROM workflow_papers WHERE id=?',(pid,)).fetchone()
        if not row:raise ContentError('NOT_FOUND','原文件不存在',404)
        path=Path(load_json(row[0])['original_copy_path']).resolve()
        if not path.is_relative_to(self.root/'.work') or not path.is_file():raise ContentError('NOT_FOUND','原文件不存在',404)
        return path

    def _legacy_id(self,qid):
        with self.store.connect() as con:row=con.execute('SELECT legacy_id FROM legacy_links WHERE question_id=?',(qid,)).fetchone()
        if not row:raise ContentError('NOT_FOUND','题目存储映射未就绪',404)
        return row[0]

    def enqueue(self,pid,qid,payload):
        if payload.keys()-{'expected_revision','intensity','teacher_feedback','request_id'}:raise ContentError('BAD_REQUEST','改写请求字段无效',400)
        question=self.store.get_question(qid)
        if question['question']['revision']!=payload.get('expected_revision') or type(payload.get('expected_revision')) is not int:raise ContentError('REVISION_CONFLICT','请先保存题目并读取最新版本',409)
        if qid not in self.store.snapshot(pid)['paper']['question_ids']:
            with self.store.connect() as con:mother=con.execute('SELECT 1 FROM workflow_candidates WHERE source_id=? AND paper_id=?',(qid,pid)).fetchone()
            if not mother:raise ContentError('NOT_FOUND','题目不属于此卷',404)
        intensity=payload.get('intensity','light');feedback=payload.get('teacher_feedback','')
        if intensity not in {'light','medium','deep'} or not isinstance(feedback,str) or len(feedback)>2000:raise ContentError('BAD_REQUEST','改写程度或教师意见无效',400)
        if not self.ai_configured():raise ContentError('GLM_CONFIG','GLM 配置尚未连接',503)
        import aivariant
        lid=self._legacy_id(qid)
        with self.legacy() as con:
            con.execute('BEGIN IMMEDIATE')
            active=con.execute("SELECT id FROM ai_jobs WHERE base_question_id=? AND status IN ('queued','running')",(lid,)).fetchone()
            if active:return self.job(active[0])
            now=aivariant._now();session=con.execute('SELECT id FROM ai_sessions WHERE base_question_id=?',(lid,)).fetchone()
            sid=session[0] if session else con.execute('INSERT INTO ai_sessions(base_question_id,created_at) VALUES (?,?)',(lid,now)).lastrowid
            seq=con.execute('SELECT COALESCE(MAX(seq),0)+1 FROM ai_versions WHERE base_question_id=?',(lid,)).fetchone()[0]
            vid=con.execute('''INSERT INTO ai_versions(session_id,base_question_id,seq,status,intensity,teacher_feedback,prompt_version,created_at,excluded)
                VALUES (?,?,?,'QUEUED',?,?,?,?,1)''',(sid,lid,seq,intensity,feedback,'content-v1',now)).lastrowid
            jid=con.execute("INSERT INTO ai_jobs(base_question_id,version_id,phase,status,attempts,created_at) VALUES (?,?,'generate','queued',0,?)",(lid,vid,now)).lastrowid
            con.execute('INSERT INTO content_jobs(job_id,paper_id,source_id,revision,snapshot,assets) VALUES (?,?,?,?,?,?)',(jid,pid,qid,question['question']['revision'],encoded(question['question']),encoded(question['assets'])))
        aivariant.start_worker()
        return self.job(jid)

    def job(self,jid):
        with self.legacy() as con:
            row=con.execute('SELECT j.id,j.status,j.version_id,c.result,c.source_id FROM ai_jobs j JOIN content_jobs c ON c.job_id=j.id WHERE j.id=?',(jid,)).fetchone()
        if not row:raise ContentError('NOT_FOUND','改写任务不存在',404)
        result=load_json(row['result']) if row['result'] else None
        return {'job_id':row['id'],'status':row['status'],'source_id':row['source_id'],'version_id':row['version_id'],'result':result}

    def candidates(self,pid):
        with self.store.connect() as con:
            rows=con.execute('SELECT * FROM workflow_candidates WHERE paper_id=? ORDER BY rowid DESC',(pid,)).fetchall()
        return {'items':[{'id':r['id'],'source_id':r['source_id'],'source_revision':r['source_revision'],'status':r['status'],'adopted':bool(r['adopted']),'version_id':r['version_id'],'result':load_json(r['result'])} for r in rows]}

    def cancel(self,jid):
        self.job(jid)
        with self.store.lock,self.legacy() as con:
            con.execute('BEGIN IMMEDIATE');row=con.execute('SELECT version_id FROM ai_jobs WHERE id=?',(jid,)).fetchone()
            con.execute("UPDATE ai_jobs SET status='cancelled' WHERE id=? AND status IN ('queued','running')",(jid,))
            con.execute("UPDATE ai_versions SET status='CANCELLED' WHERE id=? AND status IN ('QUEUED','GENERATING','JUDGING')",(row[0],))
        return self.job(jid)

    def dispatch(self,legacy_con,jid):
        row=legacy_con.execute('SELECT c.*,v.intensity,v.teacher_feedback,j.version_id FROM content_jobs c JOIN ai_jobs j ON j.id=c.job_id JOIN ai_versions v ON v.id=j.version_id WHERE c.job_id=?',(jid,)).fetchone()
        if not row:return False
        import os
        from ai_formula_v2.glm_transport import GLMClient,read_glm_config
        import aivariant
        def cancelled():
            with self.legacy() as con:
                j=con.execute('SELECT status FROM ai_jobs WHERE id=?',(jid,)).fetchone()
                return not j or j[0]=='cancelled'
        def complete(role,system,user,images):
            with self.legacy() as con:con.execute("UPDATE ai_jobs SET phase=? WHERE id=? AND status='running'",('judge' if 'proposed_answers' in user else 'generate',jid))
            return client.complete(role,system,user,images)
        try:
            client=self.client_factory() if self.client_factory else GLMClient(read_glm_config(os.environ.get('CHEM_GLM_CONFIG') or None),max_calls=2,timeout=120)
            services={'validate_question':validate_question,'compile_formula':compile_formula,'resolve_image':lambda aid:str(self.store.asset(aid,True)),
                      'complete':complete,'cancelled':cancelled,'current_revision':lambda qid:self.store.get_question(qid)['question']['revision']}
            result=aivariant.generate_content_candidate(load_json(row['snapshot']),load_json(row['assets']),services,intensity=row['intensity'],teacher_feedback=row['teacher_feedback'])
            result['usage']=deepcopy(getattr(client,'calls',[]))
        except Exception:result={'ok':False,'status':'generation_error','candidate':None,'error':{'code':'GLM_SERVICE','message':'GLM 请求未完成，请重试'}}
        self.commit_result(row['paper_id'],row['source_id'],row['revision'],result,row['version_id'],jid)
        return True

    def commit_result(self,pid,source_id,revision,result,version_id=None,job_id=None):
        with self.store.lock,self.store.connect() as con:
            con.execute('ATTACH DATABASE ? AS legacy',(str(self.legacy_path),));con.execute('BEGIN IMMEDIATE')
            source=con.execute('SELECT json FROM questions WHERE id=?',(source_id,)).fetchone()
            if job_id:
                job=con.execute('SELECT status FROM legacy.ai_jobs WHERE id=?',(job_id,)).fetchone()
                if not job or job[0]=='cancelled':return
            if not source or load_json(source[0])['revision']!=revision:
                result={'ok':False,'status':'stale','candidate':None,'error':{'code':'REVISION_CONFLICT','message':'母题已有新版本，旧响应未保存'}}
            candidate=result.get('candidate');status=result.get('status','generation_error')
            if candidate and status in {'candidate_ready','candidate_review_required','candidate_rejected'}:
                validation=validate_question(candidate,self.store._assets(con))
                if not validation['ok']:raise ContentError('VALIDATION_FAILED','候选结构验证失败',details=validation)
                con.execute('INSERT OR IGNORE INTO questions VALUES (?,?)',(candidate['id'],encoded(candidate)))
                con.execute('INSERT OR IGNORE INTO workflow_candidates VALUES (?,?,?,?,?,?,?,0)',(candidate['id'],pid,source_id,revision,status,encoded(result),version_id))
            if version_id:
                judgement=result.get('judgement',{});st={'candidate_ready':'PASS','candidate_review_required':'SUSPECT','candidate_rejected':'FAIL','cancelled':'CANCELLED','stale':'CANCELLED'}.get(status,'GENERATION_ERROR')
                con.execute('UPDATE legacy.ai_versions SET status=?,candidate_json=?,judge_json=?,judge_status=?,generator_model=?,judge_model=?,error=?,excluded=1 WHERE id=?',(st,encoded(candidate) if candidate else None,encoded(judgement),judgement.get('status'),result.get('generator_model'),judgement.get('model'),result.get('error',{}).get('message'),version_id))
            if job_id:
                con.execute('UPDATE legacy.content_jobs SET result=? WHERE job_id=?',(encoded(result),job_id))
                con.execute("UPDATE legacy.ai_jobs SET status='done' WHERE id=? AND status!='cancelled'",(job_id,))
        return candidate['id'] if candidate else None

    def adopt(self,pid,candidate_id,payload):
        if payload.keys()-{'expected_source_revision','expected_candidate_revision','request_id'}:raise ContentError('BAD_REQUEST','采纳字段无效',400)
        with self.store.lock,self.store.connect() as con:
            con.execute('ATTACH DATABASE ? AS legacy',(str(self.legacy_path),));con.execute('BEGIN IMMEDIATE')
            row=con.execute('SELECT * FROM workflow_candidates WHERE id=? AND paper_id=?',(candidate_id,pid)).fetchone()
            if not row:raise ContentError('NOT_FOUND','候选不存在',404)
            if row['status']=='candidate_rejected':raise ContentError('CONTENT_REJECTED','该候选未通过独立核对，不能采纳')
            if row['adopted']:return {'paper_id':pid,'adopted':True}
            source=load_json(con.execute('SELECT json FROM questions WHERE id=?',(row['source_id'],)).fetchone()[0])
            candidate=load_json(con.execute('SELECT json FROM questions WHERE id=?',(candidate_id,)).fetchone()[0])
            if (type(payload.get('expected_source_revision')) is not int or type(payload.get('expected_candidate_revision')) is not int or source['revision']!=row['source_revision'] or source['revision']!=payload.get('expected_source_revision') or candidate['revision']!=payload.get('expected_candidate_revision')):raise ContentError('REVISION_CONFLICT','母题或候选已更新，请重新读取再采纳',409)
            bundle=load_json(con.execute('SELECT json FROM papers WHERE id=?',(pid,)).fetchone()[0])
            ids=bundle['paper']['question_ids']
            if row['source_id'] not in ids:raise ContentError('REVISION_CONFLICT','该母题已被替换，请重新选题',409)
            # Explicit teacher action, original remains in storage/history and can be restored.
            ids[ids.index(row['source_id'])]=candidate_id
            for a in bundle['paper'].get('annotations',[]):
                if a['before_question_id']==row['source_id']:a['before_question_id']=candidate_id
            bundle['questions']=[candidate if qid==candidate_id else load_json(con.execute('SELECT json FROM questions WHERE id=?',(qid,)).fetchone()[0]) for qid in ids]
            bundle['assets']=self.store._assets(con)
            # Paper issues remain provenance evidence; point removed references to the retained mother.
            bundle['issues']=[i for i in bundle['issues'] if i.get('question_id')!=row['source_id']]
            check=validate_bundle(bundle)
            if not check['ok']:raise ContentError('VALIDATION_FAILED','采纳后的整卷结构无效',details=check)
            del bundle['questions']
            con.execute('UPDATE papers SET json=? WHERE id=?',(encoded(bundle),pid));con.execute('UPDATE workflow_candidates SET adopted=1 WHERE id=?',(candidate_id,))
            if row['version_id']:con.execute('UPDATE legacy.ai_versions SET excluded=0 WHERE id=?',(row['version_id'],))
        return {'paper_id':pid,'adopted':True}

    def restore(self,pid,candidate_id,payload):
        if payload.keys()-{'expected_candidate_revision','request_id'}:raise ContentError('BAD_REQUEST','恢复请求字段无效',400)
        with self.store.lock,self.store.connect() as con:
            con.execute('ATTACH DATABASE ? AS legacy',(str(self.legacy_path),));con.execute('BEGIN IMMEDIATE')
            row=con.execute('SELECT * FROM workflow_candidates WHERE id=? AND paper_id=?',(candidate_id,pid)).fetchone()
            if not row or not row['adopted']:raise ContentError('NOT_FOUND','本卷中没有这道已采纳候选',404)
            candidate=load_json(con.execute('SELECT json FROM questions WHERE id=?',(candidate_id,)).fetchone()[0])
            if type(payload.get('expected_candidate_revision')) is not int or candidate['revision']!=payload['expected_candidate_revision']:raise ContentError('REVISION_CONFLICT','候选已修改，请重新读取',409)
            bundle=load_json(con.execute('SELECT json FROM papers WHERE id=?',(pid,)).fetchone()[0])
            ids=bundle['paper']['question_ids']
            if candidate_id not in ids:raise ContentError('REVISION_CONFLICT','试卷引用已改变',409)
            ids[ids.index(candidate_id)]=row['source_id']
            for a in bundle['paper'].get('annotations',[]):
                if a['before_question_id']==candidate_id:a['before_question_id']=row['source_id']
            con.execute('UPDATE papers SET json=? WHERE id=?',(encoded(bundle),pid))
            con.execute('UPDATE workflow_candidates SET adopted=0 WHERE id=?',(candidate_id,))
            if row['version_id']:con.execute('UPDATE legacy.ai_versions SET excluded=1 WHERE id=?',(row['version_id'],))
        return {'paper_id':pid,'restored':True}


def load_json(value):return json.loads(value)


class ImportServices:
    def __init__(self,workflow):self.workflow=workflow
    def compile_formula(self,latex,source_ref=None):return compile_formula(latex,source_ref)
    def register_asset(self,local_path,preview_path=None):return self.workflow.store.register_asset(local_path,preview_path)
    def convert_doc(self,source,target):
        script=self.workflow.root/'scripts/convert_doc.ps1'
        completed=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(script),'-InputPath',source,'-OutputPath',target],capture_output=True,timeout=90)
        if completed.returncode:raise ContentError('DOC_CONVERSION','旧 DOC 转换未完成，原文件已保留')
