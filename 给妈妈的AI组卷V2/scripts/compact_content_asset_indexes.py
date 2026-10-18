"""Keep paper asset indexes local to their content; assets remain registered."""
import json
from pathlib import Path
import sqlite3
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from question_content.storage import nodes
path=ROOT/'.work/session-4/content-v2/content.sqlite'
con=sqlite3.connect(path,timeout=30)
question_assets={qid:{n['asset_id'] for n in nodes(json.loads(encoded)) if n.get('type')=='image'} for qid,encoded in con.execute('SELECT id,json FROM questions')}
changed=0
with con:
    for pid,encoded in con.execute('SELECT id,json FROM papers'):
        bundle=json.loads(encoded);used={n['asset_id'] for n in nodes(bundle['paper']) if n.get('type')=='image'}
        for qid in bundle['paper']['question_ids']:used.update(question_assets[qid])
        selected=[a for a in bundle['assets'] if a['id'] in used]
        if len(selected)!=len(bundle['assets']):
            bundle['assets']=selected;con.execute('UPDATE papers SET json=? WHERE id=?',(json.dumps(bundle,ensure_ascii=False),pid));changed+=1
con.close()
print('Paper-local asset indexes repaired:',changed,'; registered assets retained')
