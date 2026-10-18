"""Versioned, syntax-only recovery of remaining imported chemical text."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from question_content.http_api import ContentAPI
from question_content.compiler import compile_formula
from question_content.storage import nodes
from question_content.validation import validate_bundle,validate_question
from question_import_v2.importer import Reader

def main():
    work=ROOT/'.work/session-4/chemistry-repair';work.mkdir(exist_ok=True)
    api=ContentAPI(ROOT/'.work/session-4/content-v2',ROOT)
    with api.store.connect() as con:
        originals={r['id']:r['json'] for r in con.execute('SELECT id,json FROM questions')}
        assets=api.store._assets(con)
        papers={r['id']:json.loads(r['json']) for r in con.execute('SELECT id,json FROM papers')}
    changes={};records={};receipts=[]
    for qid,value in originals.items():
        old=json.loads(value)
        if old['origin']!='import' or old['revision']!=0 or not old['source'].get('source_sha256'):continue
        new=deepcopy(old)
        reader=object.__new__(Reader);reader.sha=old['source']['source_sha256'];reader.records={};reader.issues=[];reader.compilations={}
        reader.services=type('Services',(),{'compile_formula':staticmethod(compile_formula)})()
        reader.sid=lambda kind,locator:kind+'-'+hashlib.sha256((reader.sha+':chemistry-v5:'+qid+':'+locator).encode()).hexdigest()[:24]
        for block in list(nodes(new)):
            if block.get('type')=='paragraph':reader.restore_text_formulas(block)
        if new==old:continue
        if any(not r.get('ok') for r in reader.compilations.values()):raise ValueError('New chemical formula did not compile: '+qid)
        new['revision']=old['revision']+1
        for issue in reader.issues:issue['question_id']=qid
        new['issues'].extend(reader.issues)
        valid=validate_question(new,assets)
        if not valid['ok']:raise ValueError(str(valid))
        changes[qid]=new;records.update(reader.records)
        receipts.append({'question_id':qid,'number':old['number'],'previous_revision':old['revision'],'revision':new['revision'],'added_formulas':len(reader.compilations),'latex':[n['latex'] for n in nodes(new) if n.get('id') in reader.compilations]})
    for bundle in papers.values():
        bundle['questions']=[changes.get(qid) or json.loads(originals[qid]) for qid in bundle['paper']['question_ids']]
        used={n['asset_id'] for n in nodes(bundle) if n.get('type')=='image'}
        bundle['assets']=[a for a in assets if a['id'] in used]
        for q in bundle['questions']:
            if q['id'] in changes:bundle['issues'].extend(i for i in q['issues'] if i['id'] not in {x['id'] for x in bundle['issues']})
        valid=validate_bundle(bundle)
        if not valid['ok']:raise ValueError(str(valid))
    (work/'receipt.json').write_text(json.dumps({'questions_changed':len(changes),'new_formulas':sum(r['added_formulas'] for r in receipts),'teacher_and_ai_versions_skipped':True,'changes':receipts},ensure_ascii=False,indent=2),encoding='utf-8')
    if '--apply' not in sys.argv:print('Preview:',len(changes),'questions');return
    with api.store.connect() as con:
        backup=work/'content-before.sqlite'
        if not backup.exists():
            target=sqlite3.connect(backup);con.backup(target);target.close()
        con.execute('BEGIN IMMEDIATE')
        for qid,q in changes.items():
            if con.execute('SELECT json FROM questions WHERE id=?',(qid,)).fetchone()[0]!=originals[qid]:raise ValueError('Concurrent edit; retry without overwriting teacher: '+qid)
            con.execute('INSERT INTO history VALUES (?,?,?)',(qid,q['revision']-1,originals[qid]))
            con.execute('UPDATE questions SET json=? WHERE id=?',(json.dumps(q,ensure_ascii=False),qid))
        for ref,data in records.items():
            existing=con.execute('SELECT json FROM sources WHERE id=?',(ref,)).fetchone()
            encoded=json.dumps(data,ensure_ascii=False)
            if existing and existing[0]!=encoded:raise ValueError('Source conflict: '+ref)
            con.execute('INSERT OR IGNORE INTO sources VALUES (?,?)',(ref,encoded))
        for pid,bundle in papers.items():
            if any(qid in changes for qid in bundle['paper']['question_ids']):
                metadata={k:v for k,v in bundle.items() if k!='questions'}
                con.execute('UPDATE papers SET json=? WHERE id=?',(json.dumps(metadata,ensure_ascii=False),pid))
    for qid in changes:api.bank.sync_summary(qid)
    print('Repaired:',len(changes),'questions;',sum(r['added_formulas'] for r in receipts),'formula spans')

if __name__=='__main__':main()
