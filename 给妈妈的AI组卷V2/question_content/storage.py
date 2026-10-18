"""Isolated, atomic content store; legacy bank metadata remains separate."""
from copy import deepcopy
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import uuid

from .validation import validate_bundle, validate_question


class ContentError(Exception):
    def __init__(self, code, message, status=422, details=None):
        super().__init__(message)
        self.code, self.status, self.details = code, status, details


def nodes(value):
    if isinstance(value, dict):
        if 'type' in value: yield value
        for child in value.values(): yield from nodes(child)
    elif isinstance(value, list):
        for child in value: yield from nodes(child)


class ContentStore:
    @staticmethod
    def compile_formula(latex,source_ref=None):
        from .compiler import compile_formula
        return compile_formula(latex,source_ref)

    def __init__(self, workspace):
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True,exist_ok=True)
        self.path = self.workspace/'content.sqlite'
        self.lock = threading.RLock()
        with self.connect() as con:
            con.executescript('''
                CREATE TABLE IF NOT EXISTS questions (id TEXT PRIMARY KEY, json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS papers (id TEXT PRIMARY KEY, json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS assets (id TEXT PRIMARY KEY, json TEXT NOT NULL, path TEXT NOT NULL, preview TEXT);
                CREATE TABLE IF NOT EXISTS sources (id TEXT PRIMARY KEY, json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS history (question_id TEXT, revision INTEGER, json TEXT, PRIMARY KEY(question_id, revision));
                CREATE TABLE IF NOT EXISTS legacy_links (question_id TEXT PRIMARY KEY, legacy_id INTEGER NOT NULL, metadata TEXT NOT NULL);
            ''')

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path,timeout=10)
        con.row_factory=sqlite3.Row
        try:
            with con: yield con
        finally:con.close()

    def register_asset(self, local_path, preview_path=None):
        """Trusted importer-only Python capability, never accepts an HTTP path."""
        import mimetypes
        source=Path(local_path); data=source.read_bytes(); sha=hashlib.sha256(data).hexdigest()
        aid='asset-'+sha
        ext=source.suffix.lower()
        mime=mimetypes.guess_type(source.name)[0] or 'application/octet-stream'
        target=self.workspace/'assets'/(sha+ext); target.parent.mkdir(exist_ok=True)
        if not target.exists(): target.write_bytes(data)
        preview=None
        if preview_path:
            pdata=Path(preview_path).read_bytes(); psha=hashlib.sha256(pdata).hexdigest()
            preview=self.workspace/'assets'/(psha+Path(preview_path).suffix.lower())
            if not preview.exists(): preview.write_bytes(pdata)
        asset={'id':aid,'sha256':sha,'mime_type':mime,'preview_url':'/api/v2/content/assets/'+aid}
        with self.lock, self.connect() as con:
            con.execute('INSERT OR IGNORE INTO assets VALUES (?,?,?,?)',(aid,json.dumps(asset),str(target),str(preview) if preview else None))
            if preview:con.execute('UPDATE assets SET preview=COALESCE(preview,?) WHERE id=?',(str(preview),aid))
            registered=json.loads(con.execute('SELECT json FROM assets WHERE id=?',(aid,)).fetchone()[0])
        return registered

    def asset(self, asset_id, preview=False):
        with self.connect() as con:
            row=con.execute('SELECT * FROM assets WHERE id=?',(asset_id,)).fetchone()
        if not row: raise ContentError('ASSET_MISSING','图片资产未登记',404)
        path=Path(row['preview'] if preview and row['preview'] else row['path']).resolve()
        if not path.is_relative_to(self.workspace/'assets') or not path.is_file():
            raise ContentError('ASSET_MISSING','图片资产文件不存在或越过私有目录',404)
        if not preview and hashlib.sha256(path.read_bytes()).hexdigest()!=json.loads(row['json'])['sha256']:
            raise ContentError('ASSET_HASH','图片资产摘要发生变化')
        return path

    def register_source(self, source_ref, record):
        """Trusted importer registers original OMML/LaTeX/fallback, not browser XML."""
        self.register_sources({source_ref:record})

    def register_sources(self, records):
        """Register one document's immutable sources in a single transaction."""
        with self.lock,self.connect() as con:
            for source_ref,record in records.items():
                old=con.execute('SELECT json FROM sources WHERE id=?',(source_ref,)).fetchone()
                encoded=json.dumps(record,ensure_ascii=False)
                if old and json.loads(old[0])!=record: raise ContentError('SOURCE_CONFLICT','来源引用不可覆盖',409)
                con.execute('INSERT OR IGNORE INTO sources VALUES (?,?)',(source_ref,encoded))

    def resolve_source(self, source_ref):
        with self.connect() as con:
            row=con.execute('SELECT json FROM sources WHERE id=?',(source_ref,)).fetchone()
        return json.loads(row[0]) if row else None

    def asset_resolver(self, asset_id): return self.asset(asset_id)

    def _assets(self, con, ids=None):
        if ids is None:rows=con.execute('SELECT rowid,json FROM assets').fetchall()
        else:
            wanted=list(ids)
            if not wanted:return []
            rows=[]
            for start in range(0,len(wanted),500):
                batch=wanted[start:start+500]
                rows.extend(con.execute('SELECT rowid,json FROM assets WHERE id IN ('+','.join('?' for _ in batch)+')',batch).fetchall())
            rows.sort(key=lambda row:row['rowid'])
        return [json.loads(row['json']) for row in rows]

    def put_bundle(self, bundle):
        """Explicit trusted integration operation. No fixture auto-import at startup."""
        report=validate_bundle(bundle)
        if not report['ok']: raise ContentError('VALIDATION_FAILED','试卷结构验证失败',details=report)
        with self.lock,self.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            for a in bundle['assets']:
                row=con.execute('SELECT json FROM assets WHERE id=?',(a['id'],)).fetchone()
                if not row or json.loads(row[0])!=a: raise ContentError('ASSET_MISSING','试卷资产必须先登记并匹配摘要')
            if con.execute('SELECT 1 FROM papers WHERE id=?',(bundle['paper']['id'],)).fetchone():
                raise ContentError('PAPER_EXISTS','试卷已存在，重复导入必须显式处理',409)
            for q in bundle['questions']:
                if con.execute('SELECT 1 FROM questions WHERE id=?',(q['id'],)).fetchone():
                    raise ContentError('QUESTION_EXISTS','题目 ID 已存在，不能覆盖编辑稿',409)
                con.execute('INSERT INTO questions VALUES (?,?)',(q['id'],json.dumps(q,ensure_ascii=False)))
            metadata={k:v for k,v in bundle.items() if k!='questions'}
            con.execute('INSERT INTO papers VALUES (?,?)',(bundle['paper']['id'],json.dumps(metadata,ensure_ascii=False)))

    def get_question(self, qid):
        with self.connect() as con:
            row=con.execute('SELECT json FROM questions WHERE id=?',(qid,)).fetchone()
            if not row: raise ContentError('NOT_FOUND','题目不存在',404)
            q=json.loads(row[0]); used={n['asset_id'] for n in nodes(q) if n.get('type')=='image'}
            return {'schema_version':1,'question':q,'assets':self._assets(con,used)}

    def save_question(self,qid,expected_revision,question):
        if type(expected_revision) is not int or expected_revision<0: raise ContentError('BAD_REQUEST','expected_revision 必须为非负整数',400)
        with self.lock,self.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            row=con.execute('SELECT json FROM questions WHERE id=?',(qid,)).fetchone()
            if not row: raise ContentError('NOT_FOUND','题目不存在',404)
            old=json.loads(row[0])
            if old['revision']!=expected_revision: raise ContentError('REVISION_CONFLICT','题目已被修改，请重新读取',409,{'current_revision':old['revision']})
            if not isinstance(question,dict): raise ContentError('BAD_REQUEST','question 必须为对象',400)
            for key in ('id','revision','source','origin','issues'):
                if question.get(key)!=old[key]: raise ContentError('PROTECTED_FIELD','客户端不能修改 '+key)
            originals={n['id']:n for n in nodes(old) if n.get('type')=='formula'}
            allowed_refs={n.get('source_ref') for n in originals.values()}
            for n in nodes(question):
                if n.get('type')!='formula': continue
                previous=originals.get(n.get('id'))
                if n.get('source_ref') and n['source_ref'] not in allowed_refs:
                    raise ContentError('PROTECTED_SOURCE','浏览器不能伪造公式来源')
                if n.get('status')=='confirmed' and (previous is None or previous.get('status')!='confirmed' or n.get('latex')!=previous.get('latex')):
                    raise ContentError('FORMULA_STATUS','新公式或编辑后的公式需要后端/教师审核')
            new=deepcopy(question); new['revision']=old['revision']+1; new['origin']='teacher'
            report=validate_question(new,self._assets(con))
            if not report['ok']: raise ContentError('VALIDATION_FAILED','题目结构验证失败',details=report)
            # Validate against all papers before committing: IDs cannot collide across questions.
            for paper_row in con.execute('SELECT json FROM papers'):
                bundle=json.loads(paper_row[0])
                if qid not in bundle['paper']['question_ids']: continue
                bundle['questions']=[new if id_==qid else json.loads(con.execute('SELECT json FROM questions WHERE id=?',(id_,)).fetchone()[0]) for id_ in bundle['paper']['question_ids']]
                bundle['assets']=self._assets(con)
                whole=validate_bundle(bundle)
                if not whole['ok']: raise ContentError('VALIDATION_FAILED','整卷关联验证失败',details=whole)
            con.execute('INSERT INTO history VALUES (?,?,?)',(qid,old['revision'],row[0]))
            con.execute('UPDATE questions SET json=? WHERE id=?',(json.dumps(new,ensure_ascii=False),qid))
            used={n['asset_id'] for n in nodes(new) if n.get('type')=='image'}
            saved_assets=self._assets(con,used)
        return {'schema_version':1,'question':new,'assets':saved_assets}

    def snapshot(self,pid,expected_revisions=None):
        with self.lock,self.connect() as con:
            con.execute('BEGIN')
            row=con.execute('SELECT json FROM papers WHERE id=?',(pid,)).fetchone()
            if not row: raise ContentError('NOT_FOUND','试卷不存在',404)
            bundle=json.loads(row[0])
            bundle['questions']=[json.loads(con.execute('SELECT json FROM questions WHERE id=?',(qid,)).fetchone()[0]) for qid in bundle['paper']['question_ids']]
            actual={q['id']:q['revision'] for q in bundle['questions']}
            if expected_revisions is not None:
                if not isinstance(expected_revisions,dict) or any(type(v) is not int for v in expected_revisions.values()): raise ContentError('BAD_REQUEST','expected_revisions 必须为 ID→整数版本映射',400)
                if expected_revisions!=actual: raise ContentError('REVISION_CONFLICT','导出版本与保存版本不一致',409,{'current_revisions':actual})
            used={n['asset_id'] for n in nodes(bundle) if n.get('type')=='image'}
            bundle['assets']=self._assets(con,used)
            return bundle

    def link_legacy(self, question_id, legacy_id, metadata):
        """Retain legacy ID/classification/review/version metadata verbatim, privately."""
        with self.lock,self.connect() as con:
            con.execute('INSERT INTO legacy_links VALUES (?,?,?)',(question_id,legacy_id,json.dumps(metadata,ensure_ascii=False)))

    def write_asset_manifest(self):
        with self.connect() as con:
            records=[dict(row) for row in con.execute('SELECT * FROM assets')]
        path=self.workspace/'asset-manifest.json'
        tmp=path.with_suffix('.'+uuid.uuid4().hex+'.tmp')
        tmp.write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(path)
        return str(path)
