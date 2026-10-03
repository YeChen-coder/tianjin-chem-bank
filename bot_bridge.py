"""Narrow local interface for assistants: search existing questions, create new papers."""
import hashlib
import json
import os
import re
import sqlite3

import banklib as b
import question_analysis as qa

API_VERSION = 1
MAX_PAPER_QUESTIONS = 200


class BridgeError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _object(payload, allowed):
    if not isinstance(payload, dict):
        raise BridgeError('请求必须是一份 JSON 对象')
    unknown = set(payload) - set(allowed)
    if unknown:
        raise BridgeError('不支持的参数：' + '、'.join(sorted(unknown)))


def _integer(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise BridgeError('%s 必须是 %s 至 %s 的整数' % (name, minimum, maximum))
    return value


def _text(value, name, maximum=200):
    if not isinstance(value, str) or len(value) > maximum:
        raise BridgeError('%s 必须是最多 %s 字的文字' % (name, maximum))
    return value.strip()


def _ids(value, allow_empty=False):
    if not isinstance(value, list) or len(value) > MAX_PAPER_QUESTIONS or (not value and not allow_empty):
        raise BridgeError('题号必须是列表，每次最多 200 道题，组卷时至少选一道')
    for qid in value:
        _integer(qid, '题号', 1, 2**63 - 1)
    if len(set(value)) != len(value):
        raise BridgeError('题号有重复，请检查后再提交')
    return value


def bank_revision(con):
    digest = hashlib.sha256()
    for row in con.execute('SELECT id,dedup_key,body,qtype,major,minor,image_count,segments FROM questions WHERE '
                           + b.questions_visible_clause() + ' ORDER BY id'):
        digest.update(json.dumps(list(row), ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
        digest.update(b'\n')
    return digest.hexdigest()


def info(con):
    stats = b.compute_stats(con)
    return {'api_version': API_VERSION, 'data_directory': b.DATA_DIR,
            'bank_revision': bank_revision(con), 'question_count': stats['questions'],
            'image_question_count': stats['with_images'], 'source_file_count': stats['source_files'],
            'max_paper_questions': MAX_PAPER_QUESTIONS,
            'capabilities': ['search', 'read_questions', 'create_new_paper'],
            'note': '只检索已收入题库的题目；知识点和题型识别仍需人工核对。'}


def _images(segments):
    found = {}
    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            if node.get('t') == 'img' and node.get('src'):
                found[node['src']] = node
            else:
                for value in node.values():
                    if isinstance(value, (list, dict)):
                        walk(value)
    walk(segments)
    images = []
    for src, image in found.items():
        name = os.path.basename(src)
        valid = bool(re.fullmatch(r'/media/[0-9a-f]{64}\.[a-z0-9]+', src))
        images.append({'path': src, 'available': valid and os.path.isfile(os.path.join(b.MEDIA, name)),
                       'width': image.get('w'), 'height': image.get('h')})
    return images


def _question(con, row):
    try:
        segments = json.loads(row['segments'] or '[]')
    except (ValueError, TypeError):
        segments = []
    metadata = b.question_metadata(con, row)
    images = _images(segments)
    warnings = list(metadata.get('warnings') or [])
    if any(not image['available'] for image in images):
        warnings.append('题目有缺失或不可读取的图片，请核对原文件')
    if row['image_count'] and not images:
        warnings.append('记录显示含图，但未找到图片引用，请核对原文件')
    return {'id': row['id'], 'body': row['body'], 'answer': row['answer'],
            'qtype': row['qtype'], 'qtype_confirmed': bool(row['qtype_manual']),
            'type_suggestion': metadata.get('type_suggestion'),
            'major': row['major'], 'minor': row['minor'],
            'knowledge': metadata.get('knowledge'), 'image_count': row['image_count'],
            'images': images, 'warnings': list(dict.fromkeys(warnings)),
            'sources': [source['label'] for source in b.source_details(con, row['id'])]}


def search(con, payload):
    _object(payload, ['query', 'keywords', 'match', 'types', 'major', 'minor', 'knowledge',
                      'theme', 'has_images', 'exclude_ids', 'limit', 'offset'])
    con.row_factory = sqlite3.Row
    query = _text(payload.get('query', ''), 'query')
    keywords = payload.get('keywords', [])
    if not isinstance(keywords, list) or len(keywords) > 20:
        raise BridgeError('keywords 必须是最多 20 个词的列表')
    keywords = [_text(word, '关键词', 80) for word in keywords]
    if any(not word for word in keywords):
        raise BridgeError('关键词不能为空')
    mode = payload.get('match', 'any')
    if mode not in ('any', 'all'):
        raise BridgeError('match 只能是 any 或 all')
    types = payload.get('types', [])
    if not isinstance(types, list) or any(not isinstance(t, str) or t not in b.QTYPES for t in types):
        raise BridgeError('types 必须填写支持的中文题型')
    images = payload.get('has_images')
    if images is not None and type(images) is not bool:
        raise BridgeError('has_images 只能是 true、false 或 null')
    excluded = _ids(payload.get('exclude_ids', []), allow_empty=True)
    limit = _integer(payload.get('limit', 20), 'limit', 1, 50)
    offset = _integer(payload.get('offset', 0), 'offset', 0, 2**31 - 1)
    sql = 'SELECT * FROM questions WHERE ' + b.questions_visible_clause()
    args = []
    if query:
        sql += ' AND instr(lower(body),lower(?)) > 0'
        args.append(query)
    if keywords:
        sql += ' AND (' + (' OR ' if mode == 'any' else ' AND ').join('instr(lower(body),lower(?)) > 0' for _ in keywords) + ')'
        args.extend(keywords)
    for label in ('major', 'minor'):
        value = _text(payload.get(label, ''), label)
        if value:
            table = 'question_majors' if label == 'major' else 'question_minors'
            sql += ' AND (' + label + '=? OR id IN (SELECT question_id FROM ' + table + ' WHERE ' + label + '=?))'
            args.extend([value, value])
    knowledge = _text(payload.get('knowledge', ''), 'knowledge')
    theme = _text(payload.get('theme', ''), 'theme')
    if knowledge and knowledge not in {point['id'] for point in qa.CURRICULUM['points']}:
        raise BridgeError('知识点不存在，请先查看 catalog，使用其中的知识点 id')
    if theme and theme not in qa.CURRICULUM['themes']:
        raise BridgeError('课标主题不存在，请先查看 catalog')
    if knowledge or theme:
        points = [p for p in qa.CURRICULUM['points'] if (not knowledge or p['id'] == knowledge) and (not theme or p['theme'] == theme)]
        words = list(dict.fromkeys(word for p in points for word in p['keywords']))
        sql += ' AND (' + (' OR '.join('instr(lower(body),lower(?)) > 0' for _ in words) or '0') + ')'
        args.extend(words)
    if types:
        sql += ' AND qtype IN (' + ','.join('?' for _ in types) + ')'
        args.extend(types)
    if images is not None:
        sql += ' AND image_count' + ('>0' if images else '=0')
    if excluded:
        sql += ' AND id NOT IN (' + ','.join('?' for _ in excluded) + ')'
        args.extend(excluded)
    total = con.execute('SELECT COUNT(*) FROM (' + sql + ')', args).fetchone()[0]
    rows = con.execute(sql + ' ORDER BY id LIMIT ? OFFSET ?', args + [limit, offset]).fetchall()
    next_offset = offset + len(rows)
    return {'bank_revision': bank_revision(con), 'total': total, 'offset': offset,
            'next_offset': next_offset if next_offset < total else None,
            'items': [_question(con, row) for row in rows],
            'note': '题型筛选使用题库现有标注，知识点使用关键词建议；不能据此保证难度或试卷分值。'}


def read_questions(con, payload):
    _object(payload, ['question_ids'])
    ids = _ids(payload.get('question_ids'))
    con.row_factory = sqlite3.Row
    rows = con.execute('SELECT * FROM questions WHERE ' + b.questions_visible_clause()
                       + ' AND id IN (' + ','.join('?' for _ in ids) + ')', ids).fetchall()
    by_id = {row['id']: row for row in rows}
    missing = [qid for qid in ids if qid not in by_id]
    if missing:
        raise BridgeError('这些题号不存在或未收入题库：' + ','.join(map(str, missing)))
    return {'bank_revision': bank_revision(con), 'items': [_question(con, by_id[qid]) for qid in ids]}


def create_paper(con, payload):
    _object(payload, ['request_id', 'bank_revision', 'name', 'question_ids', 'dry_run'])
    request_id = _text(payload.get('request_id', ''), 'request_id', 128)
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', request_id):
        raise BridgeError('request_id 必须是英文、数字或 -_.:，重试同一任务时请保持不变')
    name = _text(payload.get('name', ''), '试卷名称', 80)
    if not name:
        raise BridgeError('请填写试卷名称')
    revision = _text(payload.get('bank_revision', ''), 'bank_revision', 64)
    if not re.fullmatch(r'[0-9a-f]{64}', revision):
        raise BridgeError('请先检索题库，并将返回的 bank_revision 填入计划')
    ids = _ids(payload.get('question_ids'))
    dry_run = payload.get('dry_run', False)
    if type(dry_run) is not bool:
        raise BridgeError('dry_run 必须是 true 或 false')
    fingerprint = hashlib.sha256(json.dumps([name, ids, revision], ensure_ascii=False).encode('utf-8')).hexdigest()
    con.execute('CREATE TABLE IF NOT EXISTS bot_paper_requests (request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, paper_id INTEGER NOT NULL)')
    con.execute('BEGIN IMMEDIATE')
    try:
        prior = con.execute('SELECT fingerprint,paper_id FROM bot_paper_requests WHERE request_id=?', (request_id,)).fetchone()
        if prior:
            if prior[0] != fingerprint:
                raise BridgeError('同一 request_id 已用于另一份计划，请为新任务使用新的编号', 409)
            paper = b.get_paper(con, prior[1])
            if not paper:
                raise BridgeError('这次任务创建的试卷已被删除，请为新任务使用新的编号', 409)
            con.rollback()
            result = {'paper': paper, 'count': len(paper['ids']), 'duplicate': True,
                      'preview_path': '/?paper=%d&view=1' % paper['id']}
            if dry_run:
                result['dry_run'] = True
            return result
        if revision != bank_revision(con):
            raise BridgeError('题库与检索时不同，请确认连接的是用户的题库，并重新检索后组卷', 409)
        selected = read_questions(con, {'question_ids': ids})['items']
        broken = [item['id'] for item in selected if any(not image['available'] for image in item['images'])
                  or (item['image_count'] and not item['images'])]
        if broken:
            raise BridgeError('这些题目的图片不完整，请修复或换题后组卷：' + ','.join(map(str, broken)))
        warnings = [{'question_id': item['id'], 'messages': item['warnings']} for item in selected if item['warnings']]
        if dry_run:
            con.rollback()
            return {'dry_run': True, 'name': name, 'count': len(ids), 'question_ids': ids, 'warnings': warnings}
        now = b._paper_now()
        paper_id = con.execute('INSERT INTO papers(name,updated_at) VALUES(?,?)', (name, now)).lastrowid
        con.executemany('INSERT INTO paper_items(paper_id,question_id,position) VALUES(?,?,?)',
                        [(paper_id, qid, pos) for pos, qid in enumerate(ids)])
        con.execute('INSERT INTO bot_paper_requests(request_id,fingerprint,paper_id) VALUES(?,?,?)',
                    (request_id, fingerprint, paper_id))
        con.commit()
        return {'paper': b.get_paper(con, paper_id), 'count': len(ids), 'duplicate': False,
                'preview_path': '/?paper=%d&view=1' % paper_id, 'warnings': warnings}
    except Exception:
        con.rollback()
        raise
