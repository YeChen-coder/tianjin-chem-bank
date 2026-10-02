"""Populate only the isolated preview with locally available source DOCX papers."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import banklib


if __name__ == '__main__':
    root = Path(banklib.ROOT).resolve()
    data = Path(banklib.DATA_DIR).resolve()
    if not data.is_relative_to(root / '.dev'):
        raise SystemExit('开发样本只能写入项目 .dev 目录')
    con = banklib.init_db()
    exists = con.execute('SELECT COUNT(*) FROM questions').fetchone()[0]
    con.close()
    if exists:
        print('开发题库已有数据，保留现有内容')
    else:
        for paper in sorted((root / 'imports').glob('*.docx')):
            result = banklib.import_one(str(paper), 'imports/' + paper.name)
            print('开发样本导入：%d 题' % result['parsed'])
