import hashlib
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('release_relocation',ROOT/'scripts/relocate_release.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class ReleaseRelocationTests(unittest.TestCase):
    def test_windows_source_root_binds_on_current_os_and_rejects_escape(self):
        root=ROOT.resolve()
        self.assertEqual(module.bind_path(r'C:\Old\V2\.work\file.docx','C:/Old/V2',root),str(root/'.work/file.docx'))
        self.assertEqual(module.bind_path('C:/Old/V20/file','C:/Old/V2',root),'C:/Old/V20/file')
        with self.assertRaises(ValueError):module.bind_path('C:/Old/V2/../../outside','C:/Old/V2',root)

    def test_portable_copy_preserves_number_and_verifies_images_and_sources(self):
        parent=ROOT/'.work/session-4/tests';parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as temporary:
            root=Path(temporary).resolve();data=root/'.work/session-4';assets=data/'content-v2/assets';assets.mkdir(parents=True)
            (assets/'image.png').write_bytes(b'unchanged image bytes')
            origin=data/'originals';origin.mkdir();(origin/'original.docx').write_bytes(b'unchanged original');(origin/'import-result.json').write_text('{}')
            with closing(sqlite3.connect(data/'content-v2/content.sqlite')) as con,con:
                con.executescript('CREATE TABLE assets(id,path,preview);CREATE TABLE workflow_papers(id,metadata);CREATE TABLE questions(id,json)')
                con.execute('INSERT INTO assets VALUES (?,?,NULL)',('asset-1','C:/Old/V2/.work/session-4/content-v2/assets/image.png'))
                con.execute('INSERT INTO workflow_papers VALUES (?,?)',('p',json.dumps({'original_copy_path':'C:/Old/V2/.work/session-4/originals/original.docx','import_result_path':'C:/Old/V2/.work/session-4/originals/import-result.json'})))
                con.execute('INSERT INTO questions VALUES (?,?)',('q',json.dumps({'number':'63','body':'scientific content'})))
            with closing(sqlite3.connect(data/'bank.sqlite')) as con,con:
                con.execute('CREATE TABLE questions(id,in_bank)');con.execute('INSERT INTO questions VALUES (1,1)')
            files={p.relative_to(root).as_posix():{'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in root.rglob('*') if p.is_file()}
            (root/'release-manifest.json').write_text(json.dumps({'release_id':'test','source_root':'C:/Old/V2','files':files,'bank_questions':1}))
            with patch('sys.argv',['relocate_release.py','--root',str(root)]):module.main();module.main()
            with closing(sqlite3.connect(data/'content-v2/content.sqlite')) as con,con:
                self.assertEqual(con.execute('SELECT path FROM assets').fetchone()[0],str(assets/'image.png'))
                self.assertEqual(json.loads(con.execute('SELECT json FROM questions').fetchone()[0])['number'],'63')
            self.assertEqual((assets/'image.png').read_bytes(),b'unchanged image bytes')
