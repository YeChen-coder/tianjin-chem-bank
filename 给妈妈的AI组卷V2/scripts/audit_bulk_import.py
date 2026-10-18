"""Read-only verification and a complete source-level import receipt."""
import csv
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from question_content.http_api import ContentAPI
from question_content.validation import validate_bundle
from question_content.import_selection import filename_reason

WORK=ROOT/'.work/session-4/bulk-import'
SOURCE=ROOT.parent/'chem-papers/mirror'
def write(name,data):
    (WORK/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')

def main():
    progress=json.loads((WORK/'progress.json').read_text(encoding='utf-8'))
    assert progress['done'],'Batch has not finished'
    rows=json.loads((WORK/'results.json').read_text(encoding='utf-8'))
    bypath={x['path']:x for x in rows}
    for image in json.loads((ROOT/'.work/session-4/bulk-image/results.json').read_text(encoding='utf-8')):
        if image['status']=='image_imported':
            bypath[image['path']]={**bypath[image['path']],**image,'question_count':image['questions']}
    rows=list(bypath.values())
    assert len(rows)==530
    counts=Counter(x['status'] for x in rows)
    api=ContentAPI(ROOT/'.work/session-4/content-v2',ROOT)
    issues=Counter();invalid=[];repeated=[];excluded_imported=[]
    catalog=api.workflow.catalog()['papers']
    for index,item in enumerate(catalog,1):
        bundle=api.store.snapshot(item['id'])
        report=validate_bundle(bundle)
        if not report['ok']:invalid.append({'paper':item['id'],'errors':report['errors']})
        issues.update(x['code'] for x in bundle['issues'])
        nums=Counter(q['number'] for q in bundle['questions'])
        duplicates=[n for n,c in nums.items() if c>1]
        if duplicates:repeated.append({'paper_id':item['id'],'title':item['title'],'repeated_numbers':duplicates,'reason':'同一原文件含重复题号，可能是附解析的重复题干或多个试卷；需查看来源核对，未自动删除。'})
        if index%50==0:print('Validated sources:',index,flush=True)
    checked=0
    for row in rows:
        if row.get('source_sha256'):
            assert hashlib.sha256((SOURCE/row['path']).read_bytes()).hexdigest()==row['source_sha256'],row['path']
            checked+=1
            if checked%50==0:print('Verified original hashes:',checked,flush=True)
        if row['status'] in {'imported','image_imported'} and filename_reason(SOURCE/row['path']):excluded_imported.append(row['path'])
    assert not invalid,invalid[:1]
    assert not excluded_imported,excluded_imported
    before=sqlite3.connect(WORK/'content-before.sqlite')
    with api.store.connect() as con:
        previous=list(before.execute('SELECT id,json FROM questions'))
        prior_unlinked={r[0] for r in before.execute('SELECT q.id FROM questions q LEFT JOIN legacy_links l ON q.id=l.question_id WHERE l.question_id IS NULL')}
        changed=[qid for qid,value in previous if con.execute('SELECT json FROM questions WHERE id=?',(qid,)).fetchone()[0]!=value]
        assert not changed,'Existing teacher content changed: '+str(changed)
        content_questions=con.execute('SELECT COUNT(*) FROM questions').fetchone()[0]
        links=con.execute('SELECT COUNT(*) FROM legacy_links').fetchone()[0]
        unlinked=[json.loads(r[0]) for r in con.execute('SELECT q.json FROM questions q LEFT JOIN legacy_links l ON q.id=l.question_id WHERE l.question_id IS NULL')]
        assert all(q['origin']=='ai' or q['id'] in prior_unlinked for q in unlinked),'A newly imported question has no bank link'
    before.close()
    baseline=sqlite3.connect(WORK/'bank-before.sqlite')
    with api.workflow.legacy() as con:
        tables=[r[0] for r in baseline.execute("SELECT name FROM sqlite_master WHERE type='table' AND (name='papers' OR name LIKE 'paper_%')")]
        preserved={t:list(baseline.execute('SELECT * FROM '+t))==[tuple(r) for r in con.execute('SELECT * FROM '+t)] for t in tables}
        assert all(preserved.values()),preserved
        bank_questions=con.execute('SELECT COUNT(*) FROM questions WHERE in_bank=1').fetchone()[0]
        categories={r[0]:r[1] for r in con.execute('SELECT major,COUNT(*) FROM questions WHERE in_bank=1 GROUP BY major')}
        saved_papers=con.execute('SELECT COUNT(*) FROM papers').fetchone()[0]
        jobs=[tuple(r) for r in con.execute('SELECT status,COUNT(*) FROM ai_jobs GROUP BY status')]
    baseline.close()
    assert bank_questions==links
    summary={'date':'2026-10-12','source_files':len(rows),'file_status_counts':dict(counts),'bank_questions':bank_questions,'new_questions':bank_questions-55,'content_questions':content_questions,'question_links':links,'saved_papers':saved_papers,'saved_paper_tables_preserved':preserved,'existing_questions_preserved':len(previous),'source_hashes_verified':checked,'invalid_bundles':invalid,'categories':categories,'issue_counts':dict(issues),'sources_with_repeated_numbers':len(repeated),'ai_jobs':jobs}
    write('final-results.json',rows);write('audit-summary.json',summary);write('重复题号待核对.json',repeated)
    with (WORK/'文件处理清单.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['path','status','question_count','reason','paper_id','source_sha256'],extrasaction='ignore');writer.writeheader();writer.writerows(rows)
    skips=[x for x in rows if x['status']=='skipped'];errors=[x for x in rows if x['status']=='error']
    lines=['# 原文件全量导入报告','', '日期：2026-10-12。仅写入 V2 题库；原项目、原文件只读。','',
        f'- 扫描原文件 {len(rows)} 份；文字/公式导入 {counts["imported"]} 份，图片练习导入 {counts["image_imported"]} 份，重复原卷 {counts["duplicate"]} 份，排除 {counts["skipped"]} 份，读取失败 {counts["error"]} 份。',
        f'- 当前题库 {bank_questions:,} 道；本次新增 {bank_questions-55:,} 道。图片来源 5 份、79 道保留原图并标注核对，OCR 仅辅助搜索。',
        f'- 原有题库 55 道题及内部验证内容完全保留，{saved_papers} 份已保存试卷的内容与顺序和导入前一致。',
        f'- 全部 {len(catalog)} 个来源整卷通过结构校验，{checked} 份已读取原文件的 SHA256 与记录一致。',
        f'- 自动分类已运行；{categories.get("未分类",0):,} 道未得到可靠关键词匹配，保留未分类。','',
        '## 待人工查看的内容','',
        f'- {len(repeated)} 份来源含重复题号。部分原卷把试题及带解析的题干重复排在一起；当前没有根据相似文字擅自合并，清单见「重复题号待核对.json」。',
        '- 公式转换、答案对应、分题边界的审核提示仍保留；结构验证通过不等于学科内容已人工确认。',
        '- 纯知识材料、教学材料、答题模板、高中及其他学科材料没有导入；缺乏可靠设问证据的材料也排除。混合材料可能有保守遗漏，可按下方原路径核对。','',
        '## 审核提示计数','',*['- '+k+'：'+str(v) for k,v in sorted(issues.items())], '', '## 未导入文件','']
    lines.extend('- `'+x['path']+'`：'+str(x.get('reason','无可确认试题')) for x in skips)
    if errors:lines+=['','## 读取失败','']+['- `'+x['path']+'`：'+x.get('reason','') for x in errors]
    (WORK/'导入报告.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
