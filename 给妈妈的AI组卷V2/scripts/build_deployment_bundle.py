"""Freeze a complete, private V2 release; never package keys or development backups."""
import hashlib
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import zipfile

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT.parent/'.dev/release-v2-20261015'
STAGE=OUT/'ChemBankV2'
OMIT={'__pycache__','.pytest_cache','.venv','node_modules','.git','.work'}
def digest(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def copy(path):
    path=path.resolve();relative=path.relative_to(ROOT);target=STAGE/relative
    if path.name in {'keys.local','.env'} or path.suffix in {'.pyc','.log'}:return
    target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target)
def main():
    if STAGE.exists():raise ValueError('Release stage already exists; keep existing release immutable')
    STAGE.mkdir(parents=True)
    for path in ROOT.iterdir():
        if path.is_file() and path.suffix in {'.py','.json','.txt','.md','.cmd','.ps1'}:copy(path)
    for directory in ('ai_formula_v2','docs','fixtures','formula_engine','question_content','question_import_v2','scripts','static','tests','word_export_v2'):
        for path in (ROOT/directory).rglob('*'):
            if path.is_file() and not OMIT.intersection(path.relative_to(ROOT/directory).parts):copy(path)
    data=ROOT/'.work/session-4';content=data/'content-v2/content.sqlite';bank=data/'bank.sqlite'
    # Establish a read snapshot on BOTH databases; backup from this same read
    # connection. A reserved writer lock can make backup busy indefinitely.
    lock=sqlite3.connect(bank,timeout=30);lock.execute('ATTACH DATABASE ? AS content',(str(content),));lock.execute('BEGIN')
    try:
        active=lock.execute("SELECT COUNT(*) FROM ai_jobs WHERE status IN ('queued','running')").fetchone()[0]
        if active:raise ValueError('AI jobs are active; snapshot only after they finish')
        lock.execute('SELECT COUNT(*) FROM content.questions').fetchone()
        for source in (bank,content):
            target=STAGE/source.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True)
            dst=sqlite3.connect(target);lock.backup(dst,name='main' if source==bank else 'content');dst.close()
    finally:lock.rollback();lock.close()
    with closing(sqlite3.connect(STAGE/content.relative_to(ROOT))) as con:
        if con.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Content backup is invalid')
        asset_count=con.execute('SELECT COUNT(*) FROM assets').fetchone()[0]
        for aid,path,preview in con.execute('SELECT id,path,preview FROM assets'):
            for value in (path,preview):
                if value:
                    asset=Path(value).resolve()
                    if not asset.is_relative_to(data/'content-v2/assets') or not asset.is_file():raise ValueError('Missing asset: '+aid)
                    copy(asset)
        for (encoded,) in con.execute('SELECT metadata FROM workflow_papers'):
            meta=json.loads(encoded)
            for key in ('original_copy_path','import_result_path'):
                path=Path(meta[key]).resolve()
                if not path.is_relative_to(ROOT/'.work') or not path.is_file():raise ValueError('Missing original/import record: '+key)
                copy(path)
                if (path.parent/'converted.docx').exists():copy(path.parent/'converted.docx')
    for folder in (data/'content-v2/exports',data/'media',data/'outputs'):
        if folder.exists():
            for path in folder.rglob('*'):
                if path.is_file():copy(path)
    # Reports document the full rebuild, cleanup, and known unresolved images.
    for directory in ('bulk-import','bulk-image','chemistry-repair','exam-marker-cleanup'):
        for path in (data/directory).glob('*'):
            if path.is_file() and path.suffix in {'.json','.md','.csv'}:copy(path)
    with closing(sqlite3.connect(STAGE/bank.relative_to(ROOT))) as con:
        if con.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Bank backup is invalid')
        total=con.execute('SELECT COUNT(*) FROM questions WHERE in_bank=1').fetchone()[0]
        saved=con.execute('SELECT COUNT(*) FROM papers').fetchone()[0]
    from finalize_deployment_bundle import finalize
    finalize()

if __name__=='__main__':main()
