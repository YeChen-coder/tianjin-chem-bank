"""Bind a private V2 release copy to its new root, without altering question text."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

def bind_path(value,old_root,new_root):
    if not isinstance(value,str):return value
    normalized=value.replace('\\','/');old=old_root.replace('\\','/').rstrip('/')
    if normalized.casefold()==old.casefold():return str(new_root)
    if not normalized.casefold().startswith(old.casefold()+'/'):return value
    relative=normalized[len(old)+1:];target=(new_root/relative).resolve()
    if not target.is_relative_to(new_root):raise ValueError('Release path escapes root')
    return str(target)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);args=parser.parse_args()
    root=args.root.resolve();release=json.loads((root/'release-manifest.json').read_text(encoding='utf-8'))
    receipt=root/'release-relocation.json'
    if receipt.exists():
        previous=json.loads(receipt.read_text(encoding='utf-8'))
        if previous.get('root')!=str(root):raise ValueError('This copy was already installed elsewhere; use a fresh release copy')
    else:
        # Hashes apply to the frozen release before path binding.
        for relative,info in release['files'].items():
            path=(root/relative).resolve()
            if not path.is_relative_to(root) or not path.is_file():raise ValueError('Missing release file: '+relative)
            with path.open('rb') as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
            if actual!=info['sha256']:raise ValueError('Release hash mismatch: '+relative)
    old=release['source_root'];data=root/'.work/session-4';content=data/'content-v2/content.sqlite'
    with closing(sqlite3.connect(content)) as con,con:
        for aid,path,preview in con.execute('SELECT id,path,preview FROM assets').fetchall():
            path=bind_path(path,old,root);preview=bind_path(preview,old,root)
            for value in (path,preview):
                if value and (not Path(value).resolve().is_relative_to(data/'content-v2/assets') or not Path(value).is_file()):raise ValueError('Unavailable asset: '+aid)
            con.execute('UPDATE assets SET path=?,preview=? WHERE id=?',(path,preview,aid))
        for pid,encoded in con.execute('SELECT id,metadata FROM workflow_papers').fetchall():
            meta=json.loads(encoded)
            for key in ('original_copy_path','import_result_path'):
                meta[key]=bind_path(meta[key],old,root)
                if not Path(meta[key]).resolve().is_relative_to(root/'.work') or not Path(meta[key]).is_file():raise ValueError('Unavailable source record: '+pid)
            con.execute('UPDATE workflow_papers SET metadata=? WHERE id=?',(json.dumps(meta,ensure_ascii=False),pid))
        if con.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Content integrity check failed')
    with closing(sqlite3.connect(data/'bank.sqlite')) as con,con:
        if con.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Bank integrity check failed')
        count=con.execute('SELECT COUNT(*) FROM questions WHERE in_bank=1').fetchone()[0]
        if count!=release['bank_questions']:raise ValueError('Wrong question library')
    receipt.write_text(json.dumps({'release_id':release['release_id'],'root':str(root),'bank_questions':count,'assets_and_originals_bound':True},ensure_ascii=False,indent=2),encoding='utf-8')
    print('Release paths bound; question library:',count)

if __name__=='__main__':main()
