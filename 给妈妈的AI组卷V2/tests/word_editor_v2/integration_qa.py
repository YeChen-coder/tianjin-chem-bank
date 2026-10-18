"""Ephemeral browser -> real session-4 API against a session-2 ContentStore.

No production or other-session database is accessed. Source bundle/media read only.
"""
from pathlib import Path
from http.server import ThreadingHTTPServer
import argparse, json, os, subprocess, sys, threading, uuid
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(Path(__file__).parent))
from question_content.http_api import ContentAPI
from question_content.storage import ContentStore
from mock_server import Handler

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--node',required=True);parser.add_argument('--node-path',required=True);args=parser.parse_args()
    provenance=json.loads((ROOT/'.work/session-2/imported-provenance.json').read_text(encoding='utf-8'))
    source=Path(provenance['source']);result=json.loads(source.read_text(encoding='utf-8'));bundle=result['bundle']
    manifest=json.loads((source.parent/'asset-manifest.json').read_text(encoding='utf-8'))
    workspace=ROOT/'.work/session-2/integration'/uuid.uuid4().hex;workspace.mkdir(parents=True)
    store=ContentStore(workspace)
    for asset in bundle['assets']:
        record=manifest[asset['id']]
        actual=store.register_asset(record['local_path'],record.get('preview_path'))
        if actual!=asset:raise ValueError('Shared store asset contract mismatch.')
    sources=json.loads((source.parent/'source-records.json').read_text(encoding='utf-8'))
    for ref,record in sources.items():store.register_source(ref,record)
    store.put_bundle(bundle)
    api=ContentAPI(workspace,ROOT);api._store=store
    class RealHandler(Handler):
        def _send(self,status,payload,mime,headers=None):
            data=payload.encode('utf-8') if isinstance(payload,str) else payload
            self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store')
            for key,value in (headers or {}).items():self.send_header(key,value)
            self.end_headers()
            try:self.wfile.write(data)
            except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
        def do_GET(self):
            if self.path=='/mock/catalog':return self.send(200,{'mode':'real-api','papers':[{'alias':'imported-paper','id':bundle['paper']['id'],'title':bundle['paper']['title']}]})
            if self.path=='/api/v2/content/papers/imported-paper':return self.send(200,store.snapshot(bundle['paper']['id']))
            if not api.handle(self,'GET'):super().do_GET()
        def do_POST(self):api.handle(self,'POST')
        def do_PUT(self):api.handle(self,'PUT')
    server=ThreadingHTTPServer(('127.0.0.1',0),RealHandler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    env=os.environ.copy();env['NODE_PATH']=args.node_path
    try:
        run=subprocess.run([args.node,str(Path(__file__).with_name('integration_qa.cjs')),f'http://127.0.0.1:{server.server_port}',str(workspace)],env=env,check=False)
        if run.returncode:raise SystemExit(run.returncode)
        subprocess.run([sys.executable,str(Path(__file__).with_name('verify_saved_snapshot.py')),str(workspace)],check=True)
        print(json.dumps({'workspace':str(workspace),'report':str(workspace/'browser-integration-report.json')},ensure_ascii=False))
    finally:server.shutdown();server.server_close()

if __name__=='__main__':main()
