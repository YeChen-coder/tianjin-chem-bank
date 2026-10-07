from io import BytesIO
import json
from pathlib import Path
import sys
import threading
import urllib.error
import urllib.request
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app
import banklib as b
from test_import import document, p


@pytest.fixture
def service(tmp_path, monkeypatch):
    for key, value in {'DB_PATH':str(tmp_path/'bank.sqlite'), 'DATA_DIR':str(tmp_path),
                       'MEDIA':str(tmp_path/'media'), 'MEDIA_ORIG':str(tmp_path/'media/original'),
                       'IMPORT_DIR':str(tmp_path/'imports')}.items():
        monkeypatch.setattr(b, key, value)
    monkeypatch.setenv('CHEM_DISABLE_AI', '1')
    con=b.init_db()
    questions=b.questions_from_docx(document(tmp_path, p('一、填空题') + p('1．根据质量守恒定律填写化学方程式____。') + p('2．计算配制溶液时的溶质质量分数____。')))
    b.insert_questions(con,'fixtures.docx',questions); con.commit(); con.close()
    server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
    worker=threading.Thread(target=server.serve_forever,daemon=True); worker.start()
    def request(path, payload=None, raw=None, content_type=None):
        data=json.dumps(payload,ensure_ascii=False).encode() if payload is not None else raw
        req=urllib.request.Request('http://127.0.0.1:%d%s' % (server.server_address[1],path),data=data,
                                   headers={'Content-Type':content_type or 'application/json'})
        try:
            with urllib.request.urlopen(req,timeout=5) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()
    yield request
    server.shutdown(); server.server_close(); worker.join()


def test_theme_and_knowledge_filter(service):
    status, raw=service('/api/curriculum')
    assert status==200 and len(json.loads(raw)['themes'])==5
    status, raw=service('/api/questions?knowledge=change.conservation')
    result=json.loads(raw)
    assert status==200 and result['total']==1
    assert result['items'][0]['metadata']['knowledge']['points']
    assert json.loads(service('/api/questions?knowledge=does-not-exist')[1])['total']==0


def test_paper_export_and_missing_id_error(service):
    status, raw=service('/api/papers',{'name':'验证卷','ids':[1,2]})
    assert status==200 and json.loads(raw)['ids']==[1,2]
    status, raw=service('/api/export',{'ids':[1,2]})
    assert status==200
    with zipfile.ZipFile(BytesIO(raw)) as archive:
        assert 'word/document.xml' in archive.namelist()
    status, raw=service('/api/export',{'ids':[999999]})
    assert status==400 and '不存在' in json.loads(raw)['error']


def test_disabled_ai_queues_nothing(service):
    status, raw=service('/api/questions/1/ai-variants',{'intensity':'light'})
    assert status==400 and '停用' in json.loads(raw)['error']
    con=b.open_db(); assert con.execute('SELECT COUNT(*) FROM ai_jobs').fetchone()[0]==0; con.close()


def test_import_multipart_keeps_question_boundary(service,tmp_path):
    payload=document(tmp_path,p('3．请回答空气污染的主要原因。\n4．请回答保护水资源的方法。')).read_bytes()
    raw=b'--test\r\nContent-Disposition: form-data; name="file"; filename="new.docx"\r\nContent-Type: application/octet-stream\r\n\r\n'+payload+b'\r\n--test--\r\n'
    status, result=service('/api/import',raw=raw,content_type='multipart/form-data; boundary=test')
    assert status==200 and json.loads(result)['parsed']==2
    assert json.loads(service('/api/questions')[1])['total']==4


def test_export_limit_is_explicit(service):
    status, raw=service('/api/export',{'ids':list(range(1,502))})
    assert status==400 and '500' in json.loads(raw)['error']


def test_chinese_upload_filename_and_source_export(service,tmp_path):
    name = '天津市九年级化学期末试卷（中文）.docx'
    payload = document(tmp_path,p('3．请说明保护水资源的主要措施。')).read_bytes()
    header = ('--chinese\r\nContent-Disposition: form-data; name="file"; filename="'+name+'"\r\nContent-Type: application/octet-stream\r\n\r\n').encode('utf-8')
    status, raw = service('/api/import', raw=header+payload+b'\r\n--chinese--\r\n', content_type='multipart/form-data; boundary=chinese')
    assert status == 200
    assert (Path(b.IMPORT_DIR)/name).read_bytes() == payload
    items = json.loads(service('/api/questions')[1])['items']
    item = next(item for item in items if '保护水资源' in item['body'])
    assert item['sources'] == ['imports/'+name+'（原题号 3）']
    assert item['source_details'][0]['name_status'] == 'original'
    detail = json.loads(service('/api/questions/'+str(item['id']))[1])
    assert detail['source_details'] == item['source_details']
    status, raw = service('/api/export', {'ids':[item['id']], 'keep_source':True})
    assert status == 200
    with zipfile.ZipFile(BytesIO(raw)) as archive:
        assert name in archive.read('word/document.xml').decode('utf-8')


def test_export_existing_answers_is_opt_in_and_attached_to_each_question(service):
    from xml.etree import ElementTree as ET
    con = b.open_db()
    con.execute('UPDATE questions SET answer=? WHERE id=1', ('守恒验证答案\n原有解析第二行',))
    con.execute('UPDATE questions SET answer=? WHERE id=2', ('  ',))
    con.commit(); con.close()
    for flag in (None, False, True):
        payload = {'ids': [1, 2], 'keep_source': True}
        if flag is not None:
            payload['keep_answers'] = flag
        status, raw = service('/api/export', payload)
        assert status == 200
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            root = ET.fromstring(archive.read('word/document.xml'))
        paragraphs = [''.join(p.itertext()) for p in root.findall('.//' + b.W + 'body/' + b.W + 'p')]
        text = '\n'.join(paragraphs)
        if flag:
            answer_index = next(i for i, p in enumerate(paragraphs) if '守恒验证答案' in p)
            next_question = next(i for i, p in enumerate(paragraphs) if '2. ' in p)
            assert answer_index < next_question
            assert '原有解析第二行' in paragraphs[answer_index + 1]
            assert text.count('答案：') == 1
            assert '文件来源：' in paragraphs[answer_index + 2]
        else:
            assert '守恒验证答案' not in text and '答案：' not in text
    assert service('/api/export', {'ids': [1], 'keep_answers': 'false'})[0] == 400
