"""Clean current saved content atomically; retain history and source numbering."""
from copy import deepcopy
import json
from pathlib import Path
import re
import sqlite3
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from question_content.http_api import ContentAPI
from question_content.validation import validate_bundle
from question_import_v2.cleanup import clean_question,inline_text,walk

def main():
    work=ROOT/'.work/session-4/exam-marker-cleanup';work.mkdir(exist_ok=True)
    if '--pass2' in sys.argv:work=work/'pass-2';work.mkdir(exist_ok=True)
    api=ContentAPI(ROOT/'.work/session-4/content-v2',ROOT)
    with api.store.connect() as con:
        originals={r['id']:r['json'] for r in con.execute('SELECT id,json FROM questions')}
        assets=api.store._assets(con)
        papers={r['id']:json.loads(r['json']) for r in con.execute('SELECT id,json FROM papers')}
    changes={};receipts=[];removed=set();leftovers=[]
    for qid,encoded in originals.items():
        old=json.loads(encoded);new=deepcopy(old);receipt=clean_question(new)
        if new!=old:
            assert new['source']==old['source'] and new['number']==old['number']
            new['revision']=old['revision']+1;changes[qid]=new;removed.update(receipt['removed_ids'])
            receipts.append({'question_id':qid,'source_number':old['number'],'old_revision':old['revision'],'new_revision':new['revision'],**receipt})
        first=next((b for b in new['body'] if b['type']=='paragraph' and b['inlines']),None)
        if first:
            text=''.join(inline_text(n) for n in first['inlines'])
            if re.match(r'^\s*[（(【\[]?\s*(?:本题|共)?\s*\d+(?:\.\d+)?\s*分(?:\s|[）)】\],，])',text):leftovers.append({'id':qid,'text':text[:100]})
    for bundle in papers.values():
        bundle['questions']=[changes.get(qid) or json.loads(originals[qid]) for qid in bundle['paper']['question_ids']]
        used={n['asset_id'] for n in walk(bundle) if n.get('type')=='image'}
        bundle['assets']=[a for a in assets if a['id'] in used];bundle['issues']=[i for i in bundle['issues'] if i.get('node_id') not in removed]
        valid=validate_bundle(bundle)
        if not valid['ok']:raise ValueError(str(valid))
    summary={'questions_scanned':len(originals),'questions_changed':len(changes),'prefixes_removed':sum(len(r['prefixes_removed']) for r in receipts),'identity_option_sets_removed':sum(bool(r['identity_options_removed']) for r in receipts),'source_metadata_unchanged':True,'remaining_score_prefixes':leftovers,'changes':receipts}
    (work/'receipt.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    if '--apply' not in sys.argv:print(json.dumps({k:v for k,v in summary.items() if k!='changes'},ensure_ascii=False,indent=2));return
    with api.store.connect() as con:
        if not (work/'content-before.sqlite').exists():
            target=sqlite3.connect(work/'content-before.sqlite');con.backup(target);target.close()
        con.execute('BEGIN IMMEDIATE')
        for qid,q in changes.items():
            if con.execute('SELECT json FROM questions WHERE id=?',(qid,)).fetchone()[0]!=originals[qid]:raise ValueError('Concurrent edit: '+qid)
            old_revision=q['revision']-1
            con.execute('INSERT INTO history VALUES (?,?,?)',(qid,old_revision,originals[qid]))
            con.execute('UPDATE questions SET json=? WHERE id=?',(json.dumps(q,ensure_ascii=False),qid))
            # Cosmetic source markers do not change the scientific mother used
            # by an existing candidate. Preserve already-current relationships.
            con.execute('UPDATE workflow_candidates SET source_revision=? WHERE source_id=? AND source_revision=?',(q['revision'],qid,old_revision))
        for pid,bundle in papers.items():
            if any(qid in changes for qid in bundle['paper']['question_ids']):
                metadata={k:v for k,v in bundle.items() if k!='questions'}
                con.execute('UPDATE papers SET json=? WHERE id=?',(json.dumps(metadata,ensure_ascii=False),pid))
    for qid in changes:api.bank.sync_summary(qid)
    print('Cleaned',len(changes),'questions;',summary['prefixes_removed'],'prefixes;',summary['identity_option_sets_removed'],'redundant choice sets')

if __name__=='__main__':main()
