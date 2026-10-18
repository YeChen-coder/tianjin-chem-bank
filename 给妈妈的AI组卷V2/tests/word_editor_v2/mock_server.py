"""Session 2 local HTTP mock. Writes only .work/session-2, never production data."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse
import argparse, copy, json, mimetypes, os, re, sys, threading, time, uuid

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from question_content.validation import validate_bundle, validate_question
WORK=ROOT/'.work/session-2'
LOCK=threading.RLock()
STATE={}
MODES={}
PREVIEWS={}

def write_state():
    temporary=WORK/'state.tmp'
    temporary.write_text(json.dumps(STATE,ensure_ascii=False,indent=2),encoding='utf-8')
    os.replace(temporary,WORK/'state.json')

def consume(mode):
    with LOCK:return MODES.pop(mode,False)

def find_question(qid):
    for bundle in STATE.values():
        for index,q in enumerate(bundle['questions']):
            if q['id']==qid:return bundle,index,q
    return None

def find_paper(pid):
    return STATE.get(pid) or next((b for b in STATE.values() if b['paper']['id']==pid),None)

class Handler(BaseHTTPRequestHandler):
    def log_message(self,format,*args):
        if args and str(args[1]) not in ('200','304'):super().log_message(format,*args)

    def send(self,status,payload,mime='application/json; charset=utf-8'):
        if not isinstance(payload,bytes):payload=json.dumps(payload,ensure_ascii=False).encode('utf-8')
        self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(payload)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers()
        try:self.wfile.write(payload)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass

    def error(self,status,code,message,details=None,request_id=None):
        self.send(status,{'error':{'code':code,'message':message,'details':details},'request_id':request_id})

    def body(self):
        origin=self.headers.get('Origin')
        if origin and origin not in (f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}'):raise ValueError('请从本机模拟页面操作。')
        length=int(self.headers.get('Content-Length','0'))
        if not 0<length<=2_000_000:raise ValueError('请求为空或过大。')
        if self.headers.get('Content-Type','').split(';')[0]!='application/json':raise ValueError('需要 JSON 请求。')
        value=json.loads(self.rfile.read(length).decode('utf-8'))
        if not isinstance(value,dict):raise ValueError('请求必须是对象。')
        return value

    def file(self,base,relative):
        path=(base/unquote(relative)).resolve()
        if not path.is_relative_to(base.resolve()) or not path.is_file():return self.error(404,'NOT_FOUND','文件不存在。')
        self.send(200,path.read_bytes(),mimetypes.guess_type(path.name)[0] or 'application/octet-stream')

    def do_GET(self):
        path=urlparse(self.path).path
        if path=='/':self.send_response(302);self.send_header('Location','/static/question_editor_v2/demo.html');self.end_headers();return
        if path.startswith('/static/question_editor_v2/'):return self.file(ROOT/'static/question_editor_v2',path.removeprefix('/static/question_editor_v2/'))
        for vendor in ['katex','mathlive']:
            if path.startswith(f'/vendor/{vendor}/'):
                base=ROOT/'static'/vendor
                if not base.is_dir():base=ROOT.parent/'static'/vendor  # read-only fallback
                return self.file(base,path.removeprefix(f'/vendor/{vendor}/'))
        match=re.fullmatch(r'/api/v2/content/assets/([^/]+)',path)
        if match:
            target=PREVIEWS.get(unquote(match[1]))
            if not target:return self.error(404,'ASSET_MISSING','图片资产不存在。')
            return self.file(WORK/'assets',target)
        match=re.fullmatch(r'/mock/artifacts/([a-f0-9]+)',path)
        if match:return self.file(WORK/'exports',match[1]+'.json')
        with LOCK:
            if path=='/mock/catalog':return self.send(200,{'papers':[{'alias':k,'id':b['paper']['id'],'title':b['paper']['title']} for k,b in STATE.items()]})
            match=re.fullmatch(r'/api/v2/content/papers/([^/]+)',path)
            if match:
                bundle=find_paper(unquote(match[1]))
                if bundle:return self.send(200,bundle)
                return self.error(404,'NOT_FOUND','测试试卷不存在。')
            match=re.fullmatch(r'/api/v2/content/questions/([^/]+)',path)
            if match:
                entry=find_question(unquote(match[1]))
                if entry:
                    b,_,q=entry;return self.send(200,{'schema_version':1,'question':q,'assets':b['assets']})
                return self.error(404,'NOT_FOUND','测试题目不存在。')
        self.error(404,'NOT_FOUND','资源不存在。')

    def do_POST(self):
        try:payload=self.body()
        except (ValueError,UnicodeError) as e:return self.error(400,'BAD_REQUEST',str(e))
        path=urlparse(self.path).path
        if path=='/mock/control':
            mode=payload.get('mode')
            if mode not in {'save_failure','validation_failure','conflict','slow_compile','mismatched_compile'}:return self.error(400,'BAD_REQUEST','未知模拟模式。')
            with LOCK:MODES[mode]=True
            return self.send(200,{'mock':True,'mode':mode})
        if path=='/api/v2/content/formulas/compile':
            if consume('slow_compile'):time.sleep(2)
            latex=payload.get('latex');request_id=payload.get('request_id')
            if not isinstance(latex,str) or not latex.strip() or latex.count('{')!=latex.count('}') or '\\invalid' in latex or '\\bad' in latex:
                return self.error(422,'FORMULA_COMPILE_FAILED','模拟编译失败：公式内容不完整。',request_id=request_id)
            if consume('mismatched_compile'):request_id='wrong-request-id'
            return self.send(200,{'ok':True,'latex':latex,'mathml':'<math xmlns="http://www.w3.org/1998/Math/MathML"><mtext>MOCK</mtext></math>','omml':'<m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"><m:r><m:t>MOCK</m:t></m:r></m:oMath>','compiler_version':'mock-session-2-v1','diagnostics':[{'code':'MOCK_ONLY','message':'Explicit UI stub. No actual Word compilation has occurred.'}],'request_id':request_id})
        match=re.fullmatch(r'/api/v2/content/papers/([^/]+)/export',path)
        if match:
            with LOCK:
                bundle=find_paper(unquote(match[1]))
                if not bundle:return self.error(404,'NOT_FOUND','测试试卷不存在。')
                revisions={q['id']:q['revision'] for q in bundle['questions']}
                if payload.get('expected_revisions')!=revisions:return self.error(409,'REVISION_CONFLICT','测试导出版本不一致。')
                snapshot=copy.deepcopy(bundle)
            artifact=uuid.uuid4().hex;(WORK/'exports').mkdir(exist_ok=True);(WORK/'exports'/f'{artifact}.json').write_text(json.dumps(snapshot,ensure_ascii=False,indent=2),encoding='utf-8')
            return self.send(200,{'artifact_id':artifact,'download_url':f'/mock/artifacts/{artifact}','report':{'mock':True,'message':'Only a saved JSON snapshot; Word export is session 4 responsibility.'}})
        self.error(404,'NOT_FOUND','接口不存在。')

    def do_PUT(self):
        try:payload=self.body()
        except (ValueError,UnicodeError) as e:return self.error(400,'BAD_REQUEST',str(e))
        match=re.fullmatch(r'/api/v2/content/questions/([^/]+)',urlparse(self.path).path)
        if not match:return self.error(404,'NOT_FOUND','接口不存在。')
        if consume('save_failure'):return self.error(500,'MOCK_SAVE_FAILURE','模拟保存失败。')
        if consume('validation_failure'):return self.error(422,'VALIDATION_FAILED','模拟内容验证失败。',{'errors':[{'message':'模拟结构错误'}]})
        if payload.get('schema_version')!=1 or type(payload.get('expected_revision')) is not int:return self.error(400,'BAD_REQUEST','需要协议版本及整数 expected_revision。')
        qid=unquote(match[1])
        with LOCK:
            entry=find_question(qid)
            if not entry:return self.error(404,'NOT_FOUND','题目不存在。')
            bundle,index,old=entry
            if consume('conflict'):
                old['revision']+=1;write_state()
            if payload['expected_revision']!=old['revision']:return self.error(409,'REVISION_CONFLICT','模拟版本冲突：服务器已有新版本。',{'current_revision':old['revision']})
            q=payload.get('question')
            if not isinstance(q,dict) or any(q.get(k)!=old[k] for k in ('id','revision','source','origin','issues')):return self.error(422,'PROTECTED_FIELD','题目身份、版本、来源等保护字段不能修改。')
            report=validate_question(q,bundle['assets'])
            candidate=copy.deepcopy(bundle);candidate['questions'][index]=copy.deepcopy(q);whole=validate_bundle(candidate)
            if not report['ok'] or not whole['ok']:return self.error(422,'VALIDATION_FAILED','题目结构验证失败。',whole if not whole['ok'] else report)
            q=copy.deepcopy(q);q['revision']=old['revision']+1;q['origin']='teacher';bundle['questions'][index]=q;write_state()
            self.send(200,{'schema_version':1,'question':q,'assets':bundle['assets']})

def main():
    global STATE,PREVIEWS
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8902);args=parser.parse_args()
    STATE=json.loads((WORK/'state.json').read_text(encoding='utf-8'))
    manifest=json.loads((WORK/'fixture-provenance.json').read_text(encoding='utf-8'))
    PREVIEWS={'control-image':'control.svg'}
    for item in manifest.get('asset_manifest',[]):
        filename=Path(item['destination']).name
        for b in STATE.values():
            for a in b['assets']:
                if filename.startswith(a['id']) and filename.endswith(('.png','.jpg','.jpeg','.svg')):PREVIEWS[a['id']]=filename
    private=WORK/'imported-asset-previews.json'
    if private.exists():PREVIEWS.update(json.loads(private.read_text(encoding='utf-8')))
    for name,bundle in STATE.items():
        report=validate_bundle(bundle)
        if not report['ok']:raise ValueError(json.dumps({'fixture':name,**report},ensure_ascii=False))
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    print(f'Session 2 mock only: http://127.0.0.1:{args.port}/',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()

if __name__=='__main__':main()
