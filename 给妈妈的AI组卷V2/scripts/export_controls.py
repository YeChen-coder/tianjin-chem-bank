"""Explicitly load synthetic controls into a private store and export them."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from question_content.storage import ContentStore,ContentError
from question_content.http_api import AssetResolver
from word_export_v2 import export_paper

ROOT=Path(__file__).resolve().parents[1]


def main():
    fixture=ROOT/'fixtures/word_latex_v1/synthetic'
    store=ContentStore(ROOT/'.work/session-4/content-v2')
    bundle=json.loads((fixture/'bundle.json').read_text(encoding='utf-8'))
    assert store.register_asset(fixture/'apparatus.png')==bundle['assets'][0]
    try:store.put_bundle(bundle)
    except ContentError as exc:
        if exc.code!='PAPER_EXISTS':raise
    snapshot=store.snapshot(bundle['paper']['id'])
    result=export_paper(snapshot,ROOT/'.work/session-4/outputs/controls.docx',{'repeat_table_header':True},AssetResolver(store))
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
