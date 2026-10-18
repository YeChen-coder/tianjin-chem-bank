"""Explicit user-authorized delivery merge; copies only registered assets to S4."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import banklib
from question_content.storage import ContentStore
from question_content.workflow import Workflow,load,encoded

ROOT=Path(__file__).resolve().parents[1]


def main():
    con=banklib.init_db();con.close()
    store=ContentStore(ROOT/'.work/session-4/content-v2')
    workflow=Workflow(store,ROOT)
    samples=[('e4fe1b55db17b1d7ba694e2fd0cbbac18d8c2acc08f93521530df1c765c3cc8c','期末模拟试题 · 学生卷'),
             ('560f47df6e1832032bdfcce2bd10de460be8d2b5c220ef2fb58f9e1941b764c1','天津二十中化学试卷 · 含答案解析')]
    receipts=[]
    for sha,title in samples:
        imported=load(ROOT/'.work/session-1/real-paper'/sha/'import-result.json')
        receipts.append(workflow.add_import(imported,title))
    # These are real recorded GLM outputs, explicitly labelled in the page.
    pid='paper-'+samples[0][0][:24]
    for result in load(ROOT/'.work/session-3/delivery/results.json'):
        qid=result['source_id']
        try:source=store.get_question(qid)['question']
        except Exception:continue
        if result.get('candidate'):
            result['delivery_kind']='recorded_glm';result['delivery_note']='来自已完成的真实 GLM 测试记录；采纳前请核对当前母题。'
            workflow.commit_result(pid,qid,source['revision'],result)
    out=ROOT/'.work/session-4/integration';out.mkdir(exist_ok=True)
    (out/'connection-receipts.json').write_text(encoded(receipts),encoding='utf-8')
    print(json.dumps({'papers':len(workflow.catalog()['papers']),'connected':receipts},ensure_ascii=False))


if __name__=='__main__':main()
