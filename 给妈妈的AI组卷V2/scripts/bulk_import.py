"""Resumable, read-only source scan and filtered import into the current V2 bank."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time
import shutil
from zipfile import ZipFile,is_zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from question_content.http_api import ContentAPI
from question_content.workflow import ImportServices
from question_content.import_selection import filename_reason,inspect_package,select_questions
from question_import_v2 import import_paper
from question_content.validation import validate_bundle
import banklib

WORK=ROOT/'.work/session-4/bulk-import'
SOURCE=ROOT.parent/'chem-papers/mirror'
WORK.mkdir(parents=True,exist_ok=True)


def save(path,data):
    tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    for attempt in range(20):
        try:tmp.replace(path);return
        except PermissionError:
            if attempt==19:raise
            time.sleep(.05)


def package_hash(path):
    """Ignore ZIP timestamps and document properties; keep every Word part."""
    h=hashlib.sha256()
    with ZipFile(path) as z:
        for name in sorted(n for n in z.namelist() if n.startswith('word/')):
            value=z.read(name);h.update(name.encode());h.update(hashlib.sha256(value).digest())
    return h.hexdigest()


def main():
    api=ContentAPI(ROOT/'.work/session-4/content-v2',ROOT);flow=api.workflow
    class BatchServices(ImportServices):
        def prepare_package(self,source):
            sha=hashlib.sha256(Path(source).read_bytes()).hexdigest();cached=WORK/'converted'/sha/'converted.docx'
            return str(cached) if cached.exists() else source
        def convert_doc(self,source,target):
            sha=hashlib.sha256(Path(source).read_bytes()).hexdigest();cached=WORK/'converted'/sha/'converted.docx'
            if cached.exists() and cached.resolve()!=Path(target).resolve():shutil.copyfile(cached,target)
            else:super().convert_doc(source,target)
    services=BatchServices(flow)
    paths=sorted(p for p in SOURCE.rglob('*') if p.is_file())
    previous=json.loads((WORK/'results.json').read_text(encoding='utf-8')) if (WORK/'results.json').exists() else []
    rows={r['path']:r for r in previous};memo={};existing={p['source_sha256']:p['id'] for p in flow.catalog()['papers']}
    started=time.monotonic()
    def aliases(pid,relative):
        for q in api.store.snapshot(pid)['questions']:
            lid=flow._legacy_id(q['id'])
            with flow.legacy() as con:con.execute('INSERT OR IGNORE INTO sources(question_id,rel_path,orig_qnum) VALUES (?,?,?)',(lid,relative,q['number']))
    def progress(index,current,done=False):
        values=list(rows.values());counts={}
        for r in values:counts[r['status']]=counts.get(r['status'],0)+1
        with flow.legacy() as con:total=con.execute('SELECT COUNT(*) FROM questions WHERE in_bank=1').fetchone()[0]
        save(WORK/'progress.json',{'done':done,'processed':len(rows),'scan_index':index,'total_files':len(paths),'current':current,'counts':counts,'bank_questions':total,'elapsed_seconds':round(time.monotonic()-started)})
        save(WORK/'results.json',values)
    for index,path in enumerate(paths,1):
        relative=path.relative_to(SOURCE).as_posix()
        if relative in rows and rows[relative]['status']!='error':
            old=rows[relative]
            if old.get('package_hash') and old.get('paper_id'):memo[old['package_hash']]=old['paper_id']
            progress(index,relative);continue
        row={'path':relative}
        try:
            if path.suffix.lower() not in {'.doc','.docx'}:
                row.update(status='skipped',reason='非 Word 源文件')
            elif reason:=filename_reason(path):row.update(status='skipped',reason=reason)
            else:
                before=hashlib.sha256(path.read_bytes()).hexdigest();row['source_sha256']=before
                package=path
                valid_package=False
                if is_zipfile(path):
                    with ZipFile(path) as candidate:valid_package='word/document.xml' in candidate.namelist()
                if not valid_package:
                    directory=WORK/'converted'/before;directory.mkdir(parents=True,exist_ok=True)
                    original=directory/('original'+path.suffix.lower())
                    if not original.exists():original.write_bytes(path.read_bytes())
                    package=directory/'converted.docx'
                    if not package.exists():services.convert_doc(str(original),str(package))
                check=inspect_package(package);row['screening']=check
                if not check['eligible']:row.update(status='error' if check.get('error') else 'skipped',reason=check['reason'])
                else:
                    try:fingerprint=package_hash(package)
                    except __import__('zipfile').BadZipFile:
                        from question_content.archive_repair import repair_png_crc
                        directory=WORK/'converted'/before;directory.mkdir(parents=True,exist_ok=True)
                        repaired=directory/'converted.docx';row['archive_repair']=repair_png_crc(path,repaired)
                        package=repaired;fingerprint=package_hash(package)
                    row['package_hash']=fingerprint
                    pid=existing.get(before) or memo.get(fingerprint)
                    if pid:
                        aliases(pid,relative);row.update(status='duplicate',paper_id=pid,question_count=len(api.store.snapshot(pid)['questions']))
                    else:
                        result=import_paper(path,WORK/'parsed',services)
                        selected,removed=select_questions(result);row['excluded_numbered_notes']=removed
                        if not selected['bundle']['questions']:row.update(status='skipped',reason='分题后未发现真正设问；不导入编号知识条目')
                        else:
                            report=validate_bundle(selected['bundle'])
                            if not report['ok']:raise ValueError('筛选后协议校验失败：'+json.dumps(report['errors'],ensure_ascii=False)[:1500])
                            imported=flow.add_import(selected,path.stem,create_saved_paper=False);pid=imported['paper_id'];aliases(pid,relative)
                            row.update(status='duplicate' if imported['duplicate'] else 'imported',paper_id=pid,question_count=len(selected['bundle']['questions']),formula_count=result['report']['formula_count'],issue_count=len(selected['bundle']['issues']))
                            existing[before]=pid
                    if pid:memo[fingerprint]=pid
                if hashlib.sha256(path.read_bytes()).hexdigest()!=before:raise RuntimeError('原文件处理期间发生变化')
                row['source_unchanged']=True
        except Exception as e:row.update(status='error',reason=type(e).__name__+': '+str(e)[:1800])
        rows[relative]=row;progress(index,relative)
        print(json.dumps({'processed':index,'status':row['status'],'file':relative,'questions':row.get('question_count')},ensure_ascii=False),flush=True)
    api.bank.synchronize()
    progress(len(paths),'完成',True)
    with flow.legacy() as con:
        result={'counts':{r[0]:r[1] for r in con.execute('SELECT major,COUNT(*) FROM questions WHERE in_bank=1 GROUP BY major')},'saved_papers':con.execute('SELECT COUNT(*) FROM papers').fetchone()[0]}
    save(WORK/'bank-summary.json',result)


if __name__=='__main__':main()
