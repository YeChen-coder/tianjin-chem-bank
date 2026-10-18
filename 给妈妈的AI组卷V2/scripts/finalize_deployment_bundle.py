"""Package a frozen release with all runtime files, omitting duplicate work caches."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import zipfile

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT.parent/'.dev/release-v2-20261015'
STAGE=OUT/'ChemBankV2'
PROGRAM=('ai_formula_v2','docs','fixtures','formula_engine','question_content','question_import_v2','scripts','static','tests','word_export_v2')
SKIP={'__pycache__','.pytest_cache','.venv','node_modules','.git','.work'}
def digest(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def finalize():
    files=set()
    def add(path):
        path=Path(path)
        if not path.is_file():raise ValueError('Missing runtime file: '+str(path))
        if path.name in {'keys.local','.env'} or path.name.endswith(('.sqlite-wal','.sqlite-shm')):return
        files.add(path)
    # Refresh source tooling before sealing; no release has been delivered yet.
    source=[p for p in ROOT.iterdir() if p.is_file() and p.suffix in {'.py','.json','.txt','.md','.cmd','.ps1'}]
    for directory in PROGRAM:
        source.extend(p for p in (ROOT/directory).rglob('*') if p.is_file() and not SKIP.intersection(p.relative_to(ROOT/directory).parts))
    for path in source:
        if path.suffix in {'.pyc','.log'} or path.name=='keys.local':continue
        target=STAGE/path.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target);add(target)
    bank=STAGE/'.work/session-4/bank.sqlite';content=STAGE/'.work/session-4/content-v2/content.sqlite'
    add(bank);add(content)
    print('Verifying frozen databases and collecting runtime files',flush=True)
    with closing(sqlite3.connect(content)) as con:
        if con.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Content snapshot integrity failed')
        asset_count=con.execute('SELECT COUNT(*) FROM assets').fetchone()[0]
        for aid,path,preview in con.execute('SELECT id,path,preview FROM assets'):
            for value in (path,preview):
                if value:add(STAGE/Path(value).relative_to(ROOT))
        for (encoded,) in con.execute('SELECT metadata FROM workflow_papers'):
            meta=json.loads(encoded)
            for key in ('original_copy_path','import_result_path'):
                path=STAGE/Path(meta[key]).relative_to(ROOT);add(path)
                converted=path.parent/'converted.docx'
                if converted.exists():add(converted)
    with closing(sqlite3.connect(bank)) as con:
        if con.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Bank snapshot integrity failed')
        total=con.execute('SELECT COUNT(*) FROM questions WHERE in_bank=1').fetchone()[0]
        saved=con.execute('SELECT COUNT(*) FROM papers').fetchone()[0]
    data=STAGE/'.work/session-4'
    for folder in (data/'content-v2/exports',data/'media',data/'outputs'):
        if folder.exists():
            for path in folder.rglob('*'):
                if path.is_file():add(path)
    for name in ('bulk-import','bulk-image','chemistry-repair','exam-marker-cleanup'):
        for path in (data/name).glob('*'):
            if path.is_file() and path.suffix in {'.json','.md','.csv'}:add(path)
    # Keep the second cleanup receipt as well; never bring its database backup.
    second=ROOT/'.work/session-4/exam-marker-cleanup/pass-2/receipt.json'
    if second.exists():
        target=STAGE/second.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(second,target);add(target)
    files=sorted(files)
    def fingerprint(path):return path.relative_to(STAGE).as_posix(),{'bytes':path.stat().st_size,'sha256':digest(path)}
    records={}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for index,(relative,info) in enumerate(pool.map(fingerprint,files),1):
            records[relative]=info
            if index%2000==0:print('Hashed release files:',index,'/',len(files),flush=True)
    manifest={'release_id':'chem-v2-20261015','source_root':str(ROOT),'bank_questions':total,'saved_papers':saved,'registered_assets':asset_count,'files':records,'keys_included':False,'status':'prepared_not_sent_or_deployed'}
    path=STAGE/'release-manifest.json';path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8');files.append(path)
    archive=OUT/'chem-v2-20261015-private.zip'
    print('Compressing private deployment archive',flush=True)
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=3,allowZip64=True) as z:
        for index,path in enumerate(files,1):
            z.write(path,'ChemBankV2/'+path.relative_to(STAGE).as_posix())
            if index%3000==0:print('Archived files:',index,'/',len(files),flush=True)
    summary={'release_id':manifest['release_id'],'archive':str(archive),'bytes':archive.stat().st_size,'sha256':digest(archive),'files':len(files),'bank_questions':total,'saved_papers':saved,'registered_assets':asset_count,'keys_included':False,'delivery':'not_sent','deployment':'not_started'}
    (OUT/'release.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':finalize()
