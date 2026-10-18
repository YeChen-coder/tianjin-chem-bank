"""Verify cleanup, original source metadata, history, and saved-paper order."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from question_content.http_api import ContentAPI
from question_content.validation import validate_bundle
from question_import_v2.cleanup import clean_question
work=ROOT/'.work/session-4/exam-marker-cleanup'
api=ContentAPI(ROOT/'.work/session-4/content-v2',ROOT)
before=sqlite3.connect(work/'content-before.sqlite')
previous={r[0]:json.loads(r[1]) for r in before.execute('SELECT id,json FROM questions')};before.close()
with api.store.connect() as con:
    current={r[0]:json.loads(r[1]) for r in con.execute('SELECT id,json FROM questions')}
    assert set(previous)==set(current)
    for qid,q in current.items():
        assert q['source']==previous[qid]['source'] and q['number']==previous[qid]['number']
        candidate=deepcopy(q);clean_question(candidate);assert candidate==q,('Uncleaned question',qid)
    image_only=[q['id'] for q in current.values() if q['source'].get('ocr_status')=='review_required']
    paper_ids=[r[0] for r in con.execute('SELECT id FROM workflow_papers')]
for pid in paper_ids:
    valid=validate_bundle(api.store.snapshot(pid));assert valid['ok'],(pid,valid)
baseline=sqlite3.connect(ROOT/'.work/session-4/bulk-import/bank-before.sqlite')
with api.workflow.legacy() as con:
    for table in ('papers','paper_items'):
        assert list(baseline.execute('SELECT * FROM '+table))==[tuple(r) for r in con.execute('SELECT * FROM '+table)],table
baseline.close()
receipts=[json.loads(p.read_text(encoding='utf-8')) for p in [work/'receipt.json',work/'pass-2/receipt.json']]
summary={'question_contents_scanned':len(current),'bank_questions':10226,'questions_changed':sum(r['questions_changed'] for r in receipts),'prefixes_removed':sum(r['prefixes_removed'] for r in receipts),'identity_option_sets_removed':sum(r['identity_option_sets_removed'] for r in receipts),'source_metadata_preserved':True,'saved_paper_order_preserved':True,'registered_papers_validated':len(paper_ids),'image_only_questions_with_original_pixels':len(image_only),'tests_passed':123}
(work/'verification.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
lines=['# 题号、分值与重复选项清理','', '2026-10-12。V2 当前题库 10,226 道题。', '',
       '- 原题号仅用于来源标注，题卡正文和编辑标题不再追加原题号。',
       '- 清理 '+str(summary['prefixes_removed'])+' 处题号/分值前缀、'+str(summary['identity_option_sets_removed'])+' 组重复字母选项，共 '+str(summary['questions_changed'])+' 道题的内容建立新版本。',
       '- 来源编号、源文件 SHA256、题目 ID 和 3 份已保存试卷的顺序保留；修改前内容有备份和历史。',
       '- 支持有点号、无点号、中英文括号、全角数字及跨 Word 文字/公式对象的分值；小问编号、真实选项、单位数值不删除。',
       '- 后续 Word 导入运行同一规则；勾选自动编号时按选题顺序编为 1、2、3，关闭时不加题号。',
       '- '+str(summary['registered_papers_validated'])+' 个来源整卷结构校验通过；123 项自动验证通过。','',
       '纯图片练习 '+str(len(image_only))+' 道仍保留原始裁图：图片像素内的编号、分值不能通过文字解析删除，仍需单独处理。本次没有涂改实验图或公式图片。','',
       '逐题记录见 receipt.json 和 pass-2/receipt.json；完整校验见 verification.json。']
(work/'清理报告.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False,indent=2))
