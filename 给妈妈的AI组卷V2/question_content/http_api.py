"""Schema-v1 routes, mounted before legacy routes. No fixture seeding."""
import json
import mimetypes
from pathlib import Path
import re
import uuid
from urllib.parse import unquote, urlparse

from . import compile_formula
from .storage import ContentError, ContentStore
from word_export_v2 import export_paper


class AssetResolver:
    def __init__(self,store):self.store=store
    def __call__(self,asset_id):return self.store.asset(asset_id)
    def resolve_source(self,source_ref):return self.store.resolve_source(source_ref)
    def resolve_preview(self,asset_id):return self.store.asset(asset_id,preview=True)


class ContentAPI:
    def __init__(self,workspace,root):
        self.workspace=Path(workspace).resolve();self.root=Path(root).resolve();self._store=None;self._workflow=None;self._bank=None

    @property
    def bank(self):
        if self._bank is None:
            from .bank_bridge import BankBridge
            self._bank=BankBridge(self)
        return self._bank

    @property
    def workflow(self):
        if self._workflow is None:
            from .workflow import Workflow
            self._workflow=Workflow(self.store,self.root)
        return self._workflow

    @property
    def store(self):
        if self._store is None:self._store=ContentStore(self.workspace)
        return self._store

    def send(self,h,status,obj):h._send(status,json.dumps(obj,ensure_ascii=False),'application/json; charset=utf-8')

    def handle(self,h,method):
        path=urlparse(h.path).path
        if not path.startswith('/api/v2/content/') and not path.startswith('/static/'):
            return False
        request_id=None
        try:
            if path.startswith('/static/'):
                if method!='GET':raise ContentError('NOT_FOUND','资源不存在',404)
                target=(self.root/unquote(path).lstrip('/')).resolve()
                if not target.is_relative_to(self.root/'static') or not target.is_file():raise ContentError('NOT_FOUND','资源不存在',404)
                h._send(200,target.read_bytes(),mimetypes.guess_type(str(target))[0] or 'application/octet-stream');return True
            payload={}
            if method in {'POST','PUT'}:
                origin=h.headers.get('Origin')
                if origin:
                    parsed=urlparse(origin)
                    if parsed.scheme!='http' or parsed.hostname not in {'127.0.0.1','localhost'} or parsed.port!=h.server.server_port:
                        raise ContentError('ORIGIN_REJECTED','请从本机题库页面操作',403)
                if h.headers.get('Content-Type','').split(';')[0].lower()!='application/json':raise ContentError('BAD_REQUEST','需要 application/json',400)
                try:length=int(h.headers.get('Content-Length','0'))
                except ValueError:raise ContentError('BAD_REQUEST','请求长度不合法',400)
                limit=36*1024*1024 if path=='/api/v2/content/import' else 2_000_000
                if not 0<length<=limit:raise ContentError('BAD_REQUEST','请求为空或超过大小限制',400)
                try:payload=json.loads(h.rfile.read(length).decode('utf-8'))
                except (ValueError,UnicodeError):raise ContentError('BAD_REQUEST','请求不是有效 UTF-8 JSON',400)
                if not isinstance(payload,dict):raise ContentError('BAD_REQUEST','请求必须为对象',400)
                request_id=payload.get('request_id')
                if request_id is not None and not isinstance(request_id,str):raise ContentError('BAD_REQUEST','request_id 必须为字符串或 null',400)
            question=re.fullmatch(r'/api/v2/content/questions/([^/]+)',path)
            paper=re.fullmatch(r'/api/v2/content/papers/([^/]+)',path)
            export=re.fullmatch(r'/api/v2/content/papers/([^/]+)/export',path)
            asset=re.fullmatch(r'/api/v2/content/assets/([^/]+)',path)
            artifact=re.fullmatch(r'/api/v2/content/artifacts/([a-f0-9]{32})',path)
            info=re.fullmatch(r'/api/v2/content/papers/([^/]+)/info',path)
            original=re.fullmatch(r'/api/v2/content/papers/([^/]+)/original',path)
            generate=re.fullmatch(r'/api/v2/content/papers/([^/]+)/questions/([^/]+)/ai',path)
            candidates=re.fullmatch(r'/api/v2/content/papers/([^/]+)/candidates',path)
            adopt=re.fullmatch(r'/api/v2/content/papers/([^/]+)/candidates/([^/]+)/adopt',path)
            restore=re.fullmatch(r'/api/v2/content/papers/([^/]+)/candidates/([^/]+)/restore',path)
            job=re.fullmatch(r'/api/v2/content/jobs/(\d+)',path)
            cancel=re.fullmatch(r'/api/v2/content/jobs/(\d+)/cancel',path)
            bank_paper=re.fullmatch(r'/api/v2/content/bank/papers/([^/]+)',path)
            bank_candidates=re.fullmatch(r'/api/v2/content/bank/questions/(\d+)/candidates',path)
            bank_ai=re.fullmatch(r'/api/v2/content/bank/questions/(\d+)/ai',path)
            bank_split=re.fullmatch(r'/api/v2/content/bank/questions/(\d+)/split',path)
            bank_select=re.fullmatch(r'/api/v2/content/bank/candidates/([^/]+)/select',path)
            if method=='GET' and path=='/api/v2/content/catalog':result=self.workflow.catalog()
            elif method=='POST' and path=='/api/v2/content/import':
                if payload.keys()-{'name','content_base64','request_id'}:raise ContentError('BAD_REQUEST','上传字段无效',400)
                result=self.workflow.import_upload(payload.get('name'),payload.get('content_base64'));self.bank.synchronize()
                result['paper']=self.bank.open_imported_paper(result['paper_id'])
            elif method=='GET' and bank_paper:result=self.bank.paper_link(unquote(bank_paper[1]))
            elif method=='GET' and bank_candidates:result=self.bank.candidates(int(bank_candidates[1]))
            elif method=='POST' and bank_ai:
                context=self.bank.context(int(bank_ai[1]));result=self.workflow.enqueue(context['paper_id'],context['question_id'],payload)
            elif method=='POST' and bank_select:result=self.bank.select_candidate(unquote(bank_select[1]),payload)
            elif method=='POST' and bank_split:result=self.bank.split(int(bank_split[1]),payload)
            elif method=='POST' and path=='/api/v2/content/bank/selection/export':
                snapshot=self.bank.selection(payload);aid=uuid.uuid4().hex;out=self.workspace/'exports'/(aid+'.docx')
                options=payload.get('options',{})
                exported=export_paper(snapshot,out,options,AssetResolver(self.store))
                result={'artifact_id':aid,'download_url':'/api/v2/content/artifacts/'+aid,'report':exported['report']}
            elif method=='GET' and info:result=self.workflow.paper_info(unquote(info[1]))
            elif method=='GET' and original:
                target=self.workflow.original(unquote(original[1]));h._send(200,target.read_bytes(),mimetypes.guess_type(str(target))[0] or 'application/octet-stream',{'Content-Disposition':'attachment; filename="original'+target.suffix+'"'});return True
            elif method=='GET' and candidates:result=self.workflow.candidates(unquote(candidates[1]))
            elif method=='POST' and generate:result=self.workflow.enqueue(unquote(generate[1]),unquote(generate[2]),payload)
            elif method=='POST' and adopt:result=self.workflow.adopt(unquote(adopt[1]),unquote(adopt[2]),payload)
            elif method=='POST' and restore:result=self.workflow.restore(unquote(restore[1]),unquote(restore[2]),payload)
            elif method=='GET' and job:result=self.workflow.job(int(job[1]))
            elif method=='POST' and cancel:result=self.workflow.cancel(int(cancel[1]))
            elif method=='GET' and question:result=self.store.get_question(unquote(question[1]))
            elif method=='PUT' and question:
                if payload.get('schema_version')!=1 or type(payload.get('schema_version')) is not int:raise ContentError('BAD_REQUEST','schema_version 必须为整数 1',400)
                if payload.keys()-{'schema_version','expected_revision','question','request_id'}:raise ContentError('BAD_REQUEST','不接受额外保存字段',400)
                result=self.store.save_question(unquote(question[1]),payload.get('expected_revision'),payload.get('question'))
                if self._bank is not None:self.bank.sync_summary(unquote(question[1]))
            elif method=='GET' and paper:result=self.store.snapshot(unquote(paper[1]))
            elif method=='POST' and path=='/api/v2/content/formulas/compile':
                if payload.keys()-{'latex','source_ref','request_id'}:raise ContentError('BAD_REQUEST','不接受 XML/HTML 或额外编译字段',400)
                if payload.get('source_ref') is not None and not isinstance(payload['source_ref'],str):raise ContentError('BAD_REQUEST','source_ref 必须为字符串或 null',400)
                result=compile_formula(payload.get('latex'),payload.get('source_ref'));result['request_id']=request_id
                self.send(h,200 if result['ok'] else 422,result if result['ok'] else {'error':{'code':'FORMULA_COMPILE_FAILED','message':'公式无法编译','details':result},'request_id':request_id});return True
            elif method=='POST' and export:
                if payload.keys()-{'expected_revisions','options','request_id'} or 'expected_revisions' not in payload:raise ContentError('BAD_REQUEST','导出需要完整 expected_revisions',400)
                if 'options' in payload and not isinstance(payload['options'],dict):raise ContentError('BAD_REQUEST','options 必须为对象',400)
                if not isinstance(payload['expected_revisions'],dict):raise ContentError('BAD_REQUEST','expected_revisions 必须为版本映射',400)
                snapshot=self.store.snapshot(unquote(export[1]),payload['expected_revisions'])
                aid=uuid.uuid4().hex;out=self.workspace/'exports'/(aid+'.docx')
                exported=export_paper(snapshot,out,payload.get('options',{}),AssetResolver(self.store))
                result={'artifact_id':aid,'download_url':'/api/v2/content/artifacts/'+aid,'report':exported['report']}
            elif method=='GET' and asset:
                target=self.store.asset(unquote(asset[1]),preview=True)
                h._send(200,target.read_bytes(),mimetypes.guess_type(str(target))[0] or 'application/octet-stream',{'X-Content-Type-Options':'nosniff','Content-Security-Policy':"sandbox; default-src 'none'; style-src 'unsafe-inline'"});return True
            elif method=='GET' and artifact:
                target=self.workspace/'exports'/(artifact[1]+'.docx')
                if not target.is_file():raise ContentError('NOT_FOUND','导出文件不存在',404)
                h._send(200,target.read_bytes(),'application/vnd.openxmlformats-officedocument.wordprocessingml.document',{'Content-Disposition':'attachment; filename="paper.docx"'});return True
            else:raise ContentError('NOT_FOUND','接口或资源不存在',404)
            self.send(h,200,result)
        except ContentError as exc:
            self.send(h,exc.status,{'error':{'code':exc.code,'message':str(exc),'details':exc.details},'request_id':request_id})
        except Exception:
            self.send(h,500,{'error':{'code':'SERVER_ERROR','message':'服务端处理失败，未声称保存成功','details':None},'request_id':request_id})
        return True
