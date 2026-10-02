"""Read-only legacy-bank audit. Suggestions never update the input database."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import question_analysis


def audit(path):
    con = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    with con:
        rows = con.execute("SELECT * FROM questions").fetchall()
    con.close()
    differences, unknown, glued, count = [], [], [], Counter()
    for row in rows:
        count[row['qtype']] += 1
        info = question_analysis.qtype_info(row['body'])
        if not row['qtype_manual'] and info['qtype'] != row['qtype']:
            differences.append({'id': row['id'], 'stored': row['qtype'], 'suggested': info['qtype'], 'reason': info['reason']})
        if row['major'] == '未分类':
            unknown.append(row['id'])
        import re
        if re.search(r'\n\s*(?:1[6-9]|[2-9]\d)\s*[.．、](?!\d)', row['body']):
            glued.append(row['id'])
    return {'questions': len(rows), 'stored_types': dict(count), 'type_differences': differences,
            'unclassified_ids': unknown, 'suspected_glued_ids': glued,
            'notice': '规则审计建议，未经人工标注验证，不代表真实错误率；原数据库未修改'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('database')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if Path(args.output).resolve() == Path(args.database).resolve():
        parser.error('输出文件不能覆盖输入数据库')
    result = audit(args.database)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: value if not isinstance(value, list) else len(value) for key, value in result.items()}, ensure_ascii=False))
