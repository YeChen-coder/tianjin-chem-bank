# -*- coding: utf-8 -*-
"""Answer the existing question; never create or rewrite a question.

Three isolated solutions and a separate semantic judge are kept as evidence.
Only unanimous, verified leaf answers are published to questions.answer.
"""
import hashlib
import ast
import json
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone

import banklib
import aivariant as ai

PROMPT_VERSION = 'chem-answer-v2'
PHASES = {'queued': '正在等待开始', 'plan': '正在读题、区分小问',
          'solve1': '正在第一次作答', 'solve2': '正在独立作答（第二次）',
          'solve3': '正在独立作答（第三次）', 'judge': '正在逐小问核对答案',
          'done': '答案已保存', 'cancelled': '已叫停', 'error': '作答未完成',
          'stale': '题目或答案已修改，请重新作答'}
_started = False
_start_lock = threading.Lock()

PLAN = '''你是天津九年级化学审题教师。只阅读题干与原图，不作答。
把所有需要作答的位置列为最小独立小问。一个大问内若有数个空或数个设问，必须分别列出，保留原编号并标明第几空；不要把整道实验题或简答题合成一项。
单选、多选题的选项不是小问，整道选择题只有一项。不得改题或自行补条件。
同一段里连续几个问号也要逐问拆开，不能把“使用什么燃料、观察什么现象、是否化学变化、为什么”合成一个答案。
只输出 JSON：{"parts":[{"id":"p1","label":"（1）第1空","prompt":"这一个空或设问的原文"}]}。
id 依次为 p1、p2……，label 清楚且不重复；有几处需要作答就列几项。'''
SOLVE = '''你是天津九年级化学教师，独立解答原题，不改题，不参考其他人的答案。仅用题干和实际原图的条件，限定人教版九年级范围。
逐一核对给定作答位置是否覆盖原题的每个空和小问。若漏列，请在 missing_parts 列出漏掉的原编号和设问。
对每一项给出完整答案和简短的可检查依据。计算给结果、单位和计算过程；方程式给配平与必要条件；多选给完整字母集合。
不得臆造原图未出现的试剂、液体、刻度或条件。图看不清、条件不足、答案无法确定时要明确标记，不能猜。
如果题干前后矛盾（例如文字指定的装置与连接顺序中的装置不同），不能擅自按自己认为正确的题干作答。只对受到矛盾影响的作答位置标 conditions_sufficient=false，并保留有条件的可能答案和说明。
只输出 JSON：{"coverage_complete":true,"missing_parts":[],"parts":[{"id":"p1","answer":"","reason":"简短依据（不是长篇思考过程）","conditions_sufficient":true,"image_clear":true}]}。
missing_parts 元素格式 {"label":"（2）第2空","prompt":"漏列的设问"}。每个给定 id 恰好出现一次。answer 不能是略、待补充。
conditions_sufficient、image_clear 必须是布尔。若某小问有多种合法等价表述，可给等价示例并说明；不确定的其他小问不影响这一个小问的答案。'''
JUDGE = '''你是天津九年级化学独立核对教师。现有三次互不参考的独立作答。逐小问核对科学性、条件、原图事实、数值、单位、配平、选项集合以及是否漏答。
你必须根据原题验证，不得仅因三次文字相同就判对。只接受三次都正确且语义等价的答案。合理的近义表达、化学式排版、答案顺序或正确的等价例子可以判等价；数值、单位、方程式、选项集合、必答要点不一致或有实际错误必须标记。
不能以多数票将不同的实质答案变成已确认答案。仅有分歧的小问存疑，不得把其他已一致且正确的小问一并判存疑。题目条件不足、图看不清、漏列小问也需指出。
逐条核对每次的 answer 和 reason 中的所有科学表述：最终选项正确但解析夹着错误、用途归类错误、物态变化或实验现象写错，仍不得通过。逐一填写 candidate_checks，不能漏掉任何一次或默认它正确。
核对原题全部条件是否自洽，包含文字与图示、题干指定装置与连接顺序。如果矛盾，只标记受影响的小问，不得擅自“修正原题”后判通过。
只输出 JSON：{"coverage_complete":true,"missing_parts":[],"parts":[{"id":"p1","equivalent":true,"verified":true,"answer":"经过核对的完整答案","explanation":"经过核对的简要解析","candidate_checks":[{"attempt":1,"correct":true},{"attempt":2,"correct":true},{"attempt":3,"correct":true}],"reason":"逐项验证的简短结论"}]}。
missing_parts 元素为 {"label":"","prompt":""}。每个给定 id 恰好出现一次。equivalent、verified、coverage_complete 必须是布尔。'''


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _rows(con):
    con.row_factory = sqlite3.Row


def ensure_schema(con):
    con.executescript('''
        CREATE TABLE IF NOT EXISTS answer_records (
            question_id INTEGER PRIMARY KEY, revision INTEGER NOT NULL,
            stem_hash TEXT NOT NULL, payload TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS answer_jobs (
            id INTEGER PRIMARY KEY, question_id INTEGER NOT NULL, paper_id INTEGER,
            status TEXT NOT NULL, phase TEXT NOT NULL, snapshot TEXT NOT NULL,
            result TEXT, error TEXT, created_at TEXT NOT NULL, finished_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_answer_jobs_status ON answer_jobs(status, id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_answer_jobs_active
            ON answer_jobs(question_id) WHERE status IN ('queued','running');
    ''')
    con.commit()


def stem_hash(row):
    raw = _json([row['body'], row['segments'], row['qtype']])
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _record(con, qid):
    _rows(con)
    row = con.execute('SELECT * FROM answer_records WHERE question_id=?', (qid,)).fetchone()
    return (dict(row), json.loads(row['payload'])) if row else (None, None)


def _rich_answer(con, qid):
    row = con.execute('SELECT payload FROM question_metadata WHERE question_id=?', (qid,)).fetchone()
    return (json.loads(row[0]).get('answer_segments') or []) if row else []


def _token(con, row):
    record, _ = _record(con, row['id'])
    raw = _json([stem_hash(row), row['answer'], _rich_answer(con, row['id']), record['revision'] if record else 0])
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def has_answer(con, row):
    # An image-only answer is also an existing answer and must not be overwritten.
    return banklib.answer_has_content(row['answer'], _rich_answer(con, row['id']))


def _public_job(row):
    if not row:
        return None
    job = {k: row[k] for k in ('id', 'question_id', 'status', 'phase', 'error')}
    job['message'] = PHASES.get(row['phase'], '正在处理')
    if row['status'] in ('queued', 'running') and ai._glm_paused():
        job['message'] += '；原来的 AI 服务暂时不可用，正在用备用服务继续处理'
    return job


def state(con, qid):
    _rows(con)
    row = con.execute('SELECT * FROM questions WHERE id=?', (qid,)).fetchone()
    if not row:
        return None
    record, payload = _record(con, qid)
    parts = (payload or {}).get('parts', [])
    stale = bool(record and (record['stem_hash'] != stem_hash(row)
                            or payload.get('published', '') != (row['answer'] or '')))
    if stale:
        # Never present a previous stem's verification as applying to this stem.
        parts = [dict(p, status='STALE', reason='题目或答案已修改，请老师重新核对') for p in parts]
    else:
        parts = [dict(p, status='MISSING', answer='',
                      draft=banklib.answer_text_content(p.get('draft', p.get('answer', ''))),
                      reason='只有答案标题，尚未填写实际答案')
                 if p['status'] in ('CONFIRMED', 'TEACHER') and not banklib.answer_text_content(p.get('answer', ''))
                 else p for p in parts]
    latest = con.execute('SELECT * FROM answer_jobs WHERE question_id=? ORDER BY id DESC LIMIT 1',
                         (qid,)).fetchone()
    substantive = has_answer(con, row)
    return {'question_id': qid, 'answer': banklib.answer_text_content(row['answer']) if substantive else '', 'revision': _token(con, row),
            'parts': parts, 'stale': stale, 'has_answer': substantive,
            'job': _public_job(latest), 'prompt_version': (payload or {}).get('prompt_version')}


def _publish(parts):
    lines = []
    for part in parts:
        if part['status'] not in ('CONFIRMED', 'TEACHER') or not banklib.answer_text_content(part.get('answer', '')):
            continue
        prefix = '' if len(parts) == 1 and part['label'] == '整题' else part['label'] + '：'
        text = prefix + part['answer'].strip()
        if part.get('explanation'):
            text += '\n解析：' + part['explanation'].strip()
        lines.append(text)
    return '\n'.join(lines)


def export_text(con, qid):
    """None preserves imported rich answers. Possible answers are never exported as facts."""
    record, _ = _record(con, qid)
    if not record:
        return None
    current = state(con, qid)
    lines = []
    for part in current['parts']:
        prefix = '' if len(current['parts']) == 1 and part['label'] == '整题' else part['label'] + '：'
        if part['status'] in ('CONFIRMED', 'TEACHER') and banklib.answer_text_content(part.get('answer', '')):
            lines.append(prefix + part['answer'].strip())
            if part.get('explanation'):
                lines.append('解析：' + part['explanation'].strip())
        elif part['status'] in ('SUSPECT', 'STALE'):
            lines.append(prefix + '【答案存疑，待教师确认】')
    return '\n'.join(lines)


def _write_record(con, row, payload, publish=True):
    old, _ = _record(con, row['id'])
    published = _publish(payload['parts']) if publish else (row['answer'] or '')
    payload['published'] = published
    if publish:
        con.execute('UPDATE questions SET answer=? WHERE id=?', (published, row['id']))
        # Old imported rich answer blocks must not reappear after a teacher clears/edits it.
        meta = con.execute('SELECT payload FROM question_metadata WHERE question_id=?', (row['id'],)).fetchone()
        if meta:
            value = json.loads(meta[0])
            value.pop('answer_segments', None)
            con.execute('UPDATE question_metadata SET payload=? WHERE question_id=?', (_json(value), row['id']))
        # Kept AI variants use the same linked answer, including their version panel.
        if 'ai_version_id' in row.keys() and row['ai_version_id']:
            version = con.execute('SELECT candidate_json FROM ai_versions WHERE id=?', (row['ai_version_id'],)).fetchone()
            if version and version[0]:
                candidate = json.loads(version[0])
                candidate.update(answer=published, analysis='')
                con.execute('UPDATE ai_versions SET candidate_json=? WHERE id=?', (_json(candidate), row['ai_version_id']))
    con.execute('''INSERT INTO answer_records VALUES(?,?,?,?,?)
        ON CONFLICT(question_id) DO UPDATE SET revision=excluded.revision,
        stem_hash=excluded.stem_hash,payload=excluded.payload,updated_at=excluded.updated_at''',
                (row['id'], (old['revision'] if old else 0) + 1, stem_hash(row), _json(payload), _now()))


def edit_answer(con, qid, data):
    """Optimistic autosave. Suspect drafts remain drafts until explicit teacher confirmation."""
    _rows(con)
    if not isinstance(data, dict) or not isinstance(data.get('revision'), str):
        return None, '请刷新题目后再保存答案'
    if not isinstance(data.get('answer'), str) or len(data['answer']) > 50000:
        return None, '答案格式不对或文字过长'
    if 'confirm' in data and not isinstance(data['confirm'], bool):
        return None, '确认状态格式不对'
    con.execute('BEGIN IMMEDIATE')
    try:
        row = con.execute('SELECT * FROM questions WHERE id=?', (qid,)).fetchone()
        if not row:
            return None, '题目不存在'
        if data['revision'] != _token(con, row):
            return None, '答案已在别处更新，请刷新后再保存（当前输入仍保留）'
        _, payload = _record(con, qid)
        part_id = data.get('part_id')
        answer = data['answer'].strip()
        if part_id:
            if not payload:
                return None, '小问不存在'
            part = next((p for p in payload['parts'] if p['id'] == part_id), None)
            if not part:
                return None, '小问不存在'
            current_state = state(con, qid)
            was_confirmed = part['status'] in ('CONFIRMED', 'TEACHER') and not current_state['stale']
            substantive = bool(banklib.answer_text_content(answer))
            if data.get('confirm') and not substantive:
                return None, '请先填写这一小问的实际答案，不能只有“【答案】”标题'
            part['draft'] = answer
            if data.get('confirm') or was_confirmed:
                part.update(status='TEACHER' if substantive else 'MISSING', answer=answer if substantive else '',
                            explanation='', reason='教师已修改并保存' if substantive else '尚未填写实际答案',
                            edited_at=_now())
            # Preserve stale status of the other subparts after a stem change.
            if current_state['stale']:
                for other in payload['parts']:
                    if other['id'] != part_id:
                        other.update(status='STALE', reason='题目或答案已修改，请老师重新核对')
        else:
            # Manual whole-answer editor for imported answers or a blank question.
            history = (payload or {}).get('parts', [])
            payload = {'parts': [{'id': 'p1', 'label': '整题', 'prompt': '',
                        'answer': answer if banklib.answer_text_content(answer) else '', 'draft': answer, 'explanation': '',
                        'status': 'TEACHER' if banklib.answer_text_content(answer) else 'MISSING',
                        'reason': '教师已填写并保存' if banklib.answer_text_content(answer) else '尚未填写实际答案',
                        'candidates': [], 'edited_at': _now()}], 'previous_parts': history}
        _write_record(con, row, payload)
        con.commit()
        return state(con, qid), None
    finally:
        if con.in_transaction:
            con.rollback()


def enqueue(con, paper_id, question_id=None, retry=False):
    _rows(con)
    if os.environ.get('CHEM_DISABLE_AI') == '1':
        return None, '隔离开发预览已停用 AI'
    paper = banklib.get_paper(con, paper_id)
    if not paper:
        return None, '试卷不存在'
    ids = paper['ids']
    if question_id is not None:
        if question_id not in ids:
            return None, '这道题不在当前试卷中'
        ids = [question_id]
    jobs, skipped = [], 0
    con.execute('BEGIN IMMEDIATE')
    try:
        for qid in ids:
            row = con.execute('SELECT * FROM questions WHERE id=?', (qid,)).fetchone()
            if not row:
                continue
            existing = con.execute("SELECT id FROM answer_jobs WHERE question_id=? AND status IN ('queued','running')", (qid,)).fetchone()
            if existing:
                jobs.append({'id': existing[0], 'question_id': qid, 'duplicate': True})
                continue
            _, payload = _record(con, qid)
            s = state(con, qid)
            pending = s['stale'] or any(p['status'] not in ('CONFIRMED', 'TEACHER') for p in s['parts'])
            has_candidates = bool(payload and any(
                banklib.answer_text_content(c.get('answer', '')) for p in payload['parts']
                for c in p.get('candidates', []) if isinstance(c, dict)))
            if (has_answer(con, row) or has_candidates) and not (retry and pending):
                skipped += 1
                continue
            snapshot = {'stem_hash': stem_hash(row), 'revision': _token(con, row),
                        'body': row['body'], 'qtype': row['qtype'],
                        'segments': json.loads(row['segments'] or '[]'),
                        'previous': payload if retry and not s['stale'] else None}
            cur = con.execute('''INSERT INTO answer_jobs(question_id,paper_id,status,phase,snapshot,created_at)
                              VALUES(?,?,'queued','queued',?,?)''', (qid, paper_id, _json(snapshot), _now()))
            jobs.append({'id': cur.lastrowid, 'question_id': qid, 'duplicate': False})
        con.commit()
        return {'jobs': jobs, 'skipped': skipped}, None
    finally:
        if con.in_transaction:
            con.rollback()


def stop_all(con):
    cur = con.execute("UPDATE answer_jobs SET status='cancelled',phase='cancelled',finished_at=? WHERE status IN ('queued','running')", (_now(),))
    con.commit()
    return cur.rowcount


def _phase(con, job_id, phase):
    job = con.execute('SELECT * FROM answer_jobs WHERE id=?', (job_id,)).fetchone()
    if not job or job['status'] != 'running':
        raise InterruptedError('已叫停')
    row = con.execute('SELECT * FROM questions WHERE id=?', (job['question_id'],)).fetchone()
    snapshot = json.loads(job['snapshot'])
    if not row or stem_hash(row) != snapshot['stem_hash'] or _token(con, row) != snapshot['revision']:
        con.execute("UPDATE answer_jobs SET status='stale',phase='stale',finished_at=? WHERE id=? AND status='running'", (_now(), job_id))
        con.commit()
        raise InterruptedError('题目或答案已修改')
    cur = con.execute("UPDATE answer_jobs SET phase=? WHERE id=? AND status='running'", (phase, job_id))
    con.commit()
    if cur.rowcount != 1:
        raise InterruptedError('已叫停')


def _parts(value, expected=None):
    parts = value.get('parts')
    if not isinstance(parts, list) or not 1 <= len(parts) <= 100:
        raise ValueError('AI 没有完整列出作答位置')
    ids = [p.get('id') if isinstance(p, dict) else None for p in parts]
    if any(not isinstance(p, str) or not p or len(p) > 40 for p in ids) or len(set(ids)) != len(ids):
        raise ValueError('AI 返回的小问编号重复或缺失')
    if expected is not None and set(ids) != set(expected):
        raise ValueError('AI 返回的小问数量与原题不一致')
    return parts


def _bool(value, name):
    if not isinstance(value.get(name), bool):
        raise ValueError('AI 核对结果缺少明确判断')
    return value[name]


def _missing(value):
    complete = _bool(value, 'coverage_complete')
    missing = value.get('missing_parts')
    if not isinstance(missing, list) or len(missing) > 100:
        raise ValueError('AI 没有核对作答位置是否完整')
    for part in missing:
        if not isinstance(part, dict) or not isinstance(part.get('label'), str) or not part['label'].strip() or not isinstance(part.get('prompt'), str):
            raise ValueError('AI 返回的漏答位置不明确')
    return missing, (not complete and not missing)


def _refine_plan(parts, qtype, body=''):
    blank_pattern = r'[_＿](?:[ \u2000-\u200b]*[_＿]){1,}'
    blanks = list(re.finditer(blank_pattern, body))
    # When every planned leaf is a blank, the source positions give a safer
    # outline than a model which sometimes merges a chain's two blanks.
    if qtype in ('填空题', '实验题') and blanks and all(re.search(blank_pattern, p['prompt']) for p in parts):
        markers = list(re.finditer(r'[（(](\d+)[）)]', body))
        counts, leaves = {}, []
        for blank in blanks:
            before = [m for m in markers if m.start() < blank.start()]
            marker = before[-1] if before else None
            after = next((m for m in markers if m.start() > blank.end()), None)
            section = marker[1] if marker else ''
            counts[section] = counts.get(section, 0) + 1
            start, end = (marker.start() if marker else 0), (after.start() if after else len(body))
            start, end = max(start, blank.start()-1000), min(end, blank.end()+1000)
            excerpt = body[start:blank.start()] + '【本空待答】' + body[blank.end():end]
            label = ('（' + section + '）' if section else '') + '第' + str(counts[section]) + '空'
            leaves.append({'label': label, 'prompt': '只作答下面标记出的这一空：' + excerpt})
        return [dict(p, id='p'+str(index)) for index, p in enumerate(leaves, 1)]
    refined = []
    for part in parts:
        text = part['prompt']
        if qtype not in ('单选题', '多选题') and len(re.findall(r'[？?]', text)) > 1:
            questions = [s.strip() for s in re.findall(r'[^？?]+[？?]|[^？?]+$', text) if s.strip()]
            for index, question in enumerate(questions, 1):
                refined.append({'label': part['label'] + '第' + str(index) + '问', 'prompt': question})
        else:
            refined.append(dict(part))
    return [dict(p, id='p' + str(index)) for index, p in enumerate(refined, 1)]


def _judge_has_doubt(reason):
    cleaned = re.sub(r'(?:没有|未发现|未见|不存在|无)(?:科学性?|明显|实质|计算)?(?:错误|矛盾|分歧|疑点|问题)|无误', '', reason)
    return bool(re.search(r'错误|有误|不正确|矛盾|不一致|不确定|看不清|存疑|无法确定|条件不足|有分歧|漏答', cleaned))


def _arithmetic_issue(text):
    """Check simple explicit arithmetic equalities without evaluating arbitrary code."""
    number = r'\d+(?:\.\d+)?'
    quantity = number + r'\s*(?:kg|g|mL|L|mol)?\s*'
    pattern = r'(?<![A-Za-z\d.])((?:' + quantity + r'[+\-−×*/÷]\s*)+' + quantity + r')\s*[=＝≈]\s*(' + number + r')(?![\d.])'
    def calculate(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return node.value
        if isinstance(node, ast.BinOp):
            left, right = calculate(node.left), calculate(node.right)
            if isinstance(node.op, ast.Add): return left + right
            if isinstance(node.op, ast.Sub): return left - right
            if isinstance(node.op, ast.Mult): return left * right
            if isinstance(node.op, ast.Div): return left / right
        raise ValueError('unsupported')
    for match in re.finditer(pattern, text):
        expression, answer = match.groups()
        if len(expression) > 120 or len(set(re.findall(r'kg|g|mL|L|mol', expression))) > 1:
            continue
        tail = text[match.end():match.end()+8]
        if tail.lstrip().startswith('%'):
            continue
        expression = re.sub(r'kg|g|mL|L|mol|\s', '', expression).replace('×', '*').replace('÷', '/').replace('−', '-')
        try:
            result = calculate(ast.parse(expression, mode='eval').body)
            decimals = len(answer.split('.')[1]) if '.' in answer else 0
            if abs(result - float(answer)) > .5 * 10**(-decimals) + 1e-7:
                return '计算式 ' + match.group(0).strip() + ' 的结果对不上，请老师核对'
        except (ValueError, SyntaxError, ZeroDivisionError, OverflowError):
            continue
    return ''


def _stem_conflict(body, part):
    pattern = r'用\s*([A-Z])\s*装置\s*制取[^\n]{0,250}?(?:连接|联接)顺序[^：:\n]{0,40}[:：]\s*([A-Z])\s*→'
    for match in re.finditer(pattern, body):
        if match[1] == match[2]:
            continue
        previous = list(re.finditer(r'[（(](\d+)[）)]', body[:match.start()]))
        next_label = re.search(r'[（(]\d+[）)]', body[match.end():])
        end = match.end() + next_label.start() if next_label else len(body)
        affected = previous and re.search(r'[（(]' + previous[-1][1] + r'[）)]', part['label'])
        if affected or (not previous and part['prompt'] in body[match.start():end]):
            return '题干指定用' + match[1] + '装置制取，但连接顺序从' + match[2] + '开始，条件有矛盾，请老师确认'
    return ''


def _numeric_answer_conflict(candidates, judge):
    def quantity(text):
        # A clear leading result, never numbers in the middle of a chemical formula.
        match = re.match(r'^\s*(?:[^\d\n]{0,60}?(?:为|是|[:：=＝]))?\s*(\d+(?:\.\d+)?)\s*(kg|mg|g|mL|L|千克|毫克|克|毫升|升|%)(?=[\s。.;；,，]|$)', text)
        if not match:
            return None
        units = {'kg': ('mass', 1000), 'g': ('mass', 1), 'mg': ('mass', .001),
                 '千克': ('mass', 1000), '克': ('mass', 1), '毫克': ('mass', .001),
                 'L': ('volume', 1000), 'mL': ('volume', 1), '升': ('volume', 1000),
                 '毫升': ('volume', 1), '%': ('percent', 1)}
        dimension, factor = units[match[2]]
        return dimension, float(match[1]) * factor
    values = [value for c in candidates if (value := quantity(c['answer'])) is not None]
    final = quantity(judge.get('answer', ''))
    if final is not None:
        values.append(final)
    for index, (dimension, value) in enumerate(values):
        if any(other_dimension == dimension and abs(other_value-value) > max(1e-8, abs(value)*1e-8)
               for other_dimension, other_value in values[index+1:]):
            return '几次作答给出的数值对不上，请老师核对这一小问'
    return ''


def _choice_set(text):
    text = re.sub(r'^\s*(?:答案\s*(?:为|是|[:：])?|正确选项\s*(?:为|是|[:：])?|选项|选择|选)\s*', '', text.strip())
    match = re.match(r'^([A-H](?:[\s、,，;；]*[A-H])*)(?=$|[。.(（:：]|[^A-Za-z])', text.upper())
    if not match or re.match(r'^\s*(?:或|和|及|OR\b)', text[match.end():].upper()):
        return None
    return frozenset(re.findall(r'[A-H]', match[1]))


def _equation_issue(text):
    # Common grade-9 solid products must not be labelled as an escaping gas.
    wrong = re.search(r'(?:MnO[2₂]|CuO|Fe[2₂]O[3₃]|CaCO[3₃]|BaSO[4₄]|AgCl)\s*↑', text)
    return '方程式把' + wrong[0][:-1].strip() + '标成逸出的气体，请老师核对' if wrong else ''


def _call(system, data, images, role, independent=False):
    preferred = 'deepseek' if independent and ai._glm_key and ai._ds_key else None
    if preferred:
        raw, model = ai.complete_role(role, system, _json(data), images, preferred_provider=preferred)
    else:
        raw, model = ai.complete_role(role, system, _json(data), images)
    return ai.parse_model_json(raw), model


def evaluate(snapshot, progress):
    """No shared chat history: solvers never see any other solver's answer."""
    images = ai._image_paths(snapshot['segments'])
    role = 'flash'
    stem = {k: snapshot[k] for k in ('body', 'qtype', 'segments')}
    progress('plan')
    previous_parts = (snapshot.get('previous') or {}).get('parts') or []
    if previous_parts and all(p.get('prompt') for p in previous_parts):
        # Keep reviewed leaf identities stable when retrying unresolved subparts.
        plan = {'parts': [{k: p[k] for k in ('id', 'label', 'prompt')} for p in previous_parts]}
        model = 'saved-plan'
    else:
        plan, model = _call(PLAN, stem, images, role)
    planned = _parts(plan)
    labels = set()
    for part in planned:
        for key in ('label', 'prompt'):
            if not isinstance(part.get(key), str) or not part[key].strip() or len(part[key]) > 4000:
                raise ValueError('AI 没有清楚列出各小问')
        if part['label'] in labels:
            raise ValueError('AI 返回的小问名称重复')
        labels.add(part['label'])
    if not previous_parts:
        planned = _refine_plan(planned, snapshot['qtype'], snapshot['body'])
    if len(planned) > 100:
        raise ValueError('作答位置过多，请先检查是否把几道题连在一起了')
    ids = [p['id'] for p in planned]
    request = dict(stem, parts=planned)
    solutions, failures, missing_parts = [], [], []
    unknown_coverage = False
    for number in range(1, 4):
        progress('solve' + str(number))
        try:
            solver_role = 'pro' if number == 3 and not images else role
            value, solver_model = _call(SOLVE, request, images, solver_role, independent=(number == 2))
            parts = _parts(value, ids)
            missing, unclear = _missing(value)
            for part in parts:
                if not isinstance(part.get('answer'), str) or len(part['answer']) > 20000 or not isinstance(part.get('reason'), str) or len(part['reason']) > 12000:
                    raise ValueError('AI 没有返回可检查的完整答案')
                _bool(part, 'conditions_sufficient')
                _bool(part, 'image_clear')
            solutions.append({'attempt': number, 'model': solver_model, 'parts': parts,
                              'coverage_complete': value['coverage_complete'], 'missing_parts': missing})
            missing_parts.extend(missing)
            unknown_coverage |= unclear
        except (ai.ModelError, ValueError, TypeError, KeyError) as exc:
            failures.append({'attempt': number, 'error': ai._safe_text(exc)})
    progress('judge')
    judgement, judge_model, judge_error = {}, '', ''
    if solutions:
        try:
            # The judge gets the actual image again, not a previous model's description.
            judge_role = 'pro' if not images else role
            judgement, judge_model = _call(JUDGE, dict(request, solutions=solutions), images, judge_role, independent=True)
            _parts(judgement, ids)
            missing, unclear = _missing(judgement)
            missing_parts.extend(missing)
            unknown_coverage |= unclear
            for part in judgement['parts']:
                _bool(part, 'equivalent')
                _bool(part, 'verified')
                if not isinstance(part.get('reason'), str) or not part['reason'].strip() or len(part['reason']) > 12000:
                    raise ValueError('AI 没有给出核对依据')
                checks = part.get('candidate_checks')
                attempts = [s['attempt'] for s in solutions]
                if not isinstance(checks, list) or len(checks) != len(attempts):
                    raise ValueError('AI 没有逐次核对答案和解析')
                if any(not isinstance(c, dict) or not isinstance(c.get('attempt'), int) or isinstance(c['attempt'], bool) or not isinstance(c.get('correct'), bool) for c in checks):
                    raise ValueError('AI 逐次核对结果格式不正确')
                if sorted(c['attempt'] for c in checks) != sorted(attempts):
                    raise ValueError('AI 逐次核对编号不完整')
                for key in ('answer', 'explanation'):
                    if not isinstance(part.get(key), str) or len(part[key]) > 20000:
                        raise ValueError('AI 没有返回经过核对的答案和解析')
        except (ai.ModelError, ValueError, TypeError, KeyError) as exc:
            judgement = {}
            judge_error = ai._safe_text(exc)
    judged = {p['id']: p for p in judgement.get('parts', [])}
    output = []
    for planned_part in planned:
        pid = planned_part['id']
        candidates = []
        for solution in solutions:
            part = next(p for p in solution['parts'] if p['id'] == pid)
            candidates.append(dict(part, attempt=solution['attempt'], model=solution['model']))
        judge = judged.get(pid, {})
        full = len(candidates) == 3 and all(p['conditions_sufficient'] and p['image_clear']
                and banklib.answer_text_content(p['answer']) and p['answer'].strip() not in ('略', '待补充', '无', '答案略')
                and p['reason'].strip() for p in candidates)
        checked = judge.get('candidate_checks', [])
        verified = (full and not unknown_coverage and judge.get('equivalent') is True
                    and judge.get('verified') is True and len(checked) == 3
                    and all(c['correct'] for c in checked) and banklib.answer_text_content(judge.get('answer', ''))
                    and judge.get('explanation', '').strip())
        if snapshot['qtype'] in ('单选题', '多选题'):
            # A semantic judge must not override conflicting option letters.
            letter_sets = []
            for candidate in candidates:
                letter_sets.append(_choice_set(candidate['answer']))
            if len(letter_sets) != 3 or any(s is None for s in letter_sets) or len(set(letter_sets)) != 1:
                verified = False
                judge = dict(judge, reason='几次作答的选项未能明确一致，请老师确认')
            elif judge.get('answer'):
                canonical = _choice_set(judge['answer'])
                if canonical != letter_sets[0]:
                    verified = False
                    judge = dict(judge, reason='核对后的选项与独立作答不一致，请老师确认')
        reason = judge.get('reason') or '独立核对未完成，需老师确认'
        if any(not c['conditions_sufficient'] for c in candidates):
            reason = '有一次作答认为原题条件不足，请老师核对；' + reason
        if any(not c['image_clear'] for c in candidates):
            reason = '有一次作答未看清原图，请老师核对；' + reason
        if failures or judge_error:
            reason = '独立作答或核对未完成，需老师确认；' + reason
        if unknown_coverage:
            reason = '作答位置可能漏列，需老师核对原题；' + reason
        first = candidates[0] if candidates else {}
        conflict = _stem_conflict(snapshot['body'], planned_part)
        arithmetic = next((issue for c in candidates if (issue := _arithmetic_issue(c['answer'] + '\n' + c['reason']))), '')
        arithmetic = arithmetic or _arithmetic_issue(judge.get('answer', '') + '\n' + judge.get('explanation', ''))
        numerical_disagreement = _numeric_answer_conflict(candidates, judge)
        equation = next((issue for c in candidates if (issue := _equation_issue(c['answer'] + '\n' + c['reason']))), '')
        equation = equation or _equation_issue(judge.get('answer', '') + '\n' + judge.get('explanation', ''))
        if conflict or arithmetic or numerical_disagreement or equation or _judge_has_doubt(reason):
            verified = False
            reason = conflict or arithmetic or numerical_disagreement or equation or ('核对说明仍有疑点；' + reason)
        output.append(dict(planned_part, status='CONFIRMED' if verified else 'SUSPECT',
                           answer=judge.get('answer', '') if verified else '',
                           explanation=judge.get('explanation', '') if verified else '',
                           draft=judge.get('answer', '') if verified else first.get('answer', ''), reason=reason, candidates=candidates,
                           judge=judge, judge_model=judge_model))
    # Missing leaf questions do not invalidate verified siblings. Their absence is explicit.
    seen_missing = set()
    for part in missing_parts:
        key = (part['label'].strip(), part['prompt'].strip())
        if key in seen_missing:
            continue
        seen_missing.add(key)
        matching = next((p for p in output if p['label'].strip() == key[0]), None)
        if matching:
            matching.update(status='SUSPECT', answer='', explanation='',
                            reason='独立检查认为这一小问尚未完整作答，请老师确认')
            continue
        output.append(dict(id='missing-' + str(len(seen_missing)), label=key[0], prompt=key[1],
                           status='SUSPECT', answer='', draft='', explanation='', candidates=[],
                           reason='独立检查发现这一处漏答，请老师补充确认'))
    # Retrying unresolved answers must preserve previously confirmed teacher/AI answers.
    previous = snapshot.get('previous') or {}
    if previous.get('parts'):
        preserved = {p['id']: p for p in previous['parts']
                     if p['status'] in ('CONFIRMED', 'TEACHER')
                     and banklib.answer_text_content(p.get('answer', ''))}
        # Retry planning may number parts differently; match by exact label + prompt, never by ID alone.
        for index, part in enumerate(output):
            old = next((p for p in preserved.values() if p['label'] == part['label'] and p['prompt'] == part['prompt']), None)
            if old:
                output[index] = dict(old, id=part['id'])
        matched = {(p['label'], p['prompt']) for p in output}
        for old in preserved.values():
            if (old['label'], old['prompt']) not in matched:
                output.append(dict(old, id='preserved-' + old['id']))
    return {'parts': output, 'prompt_version': PROMPT_VERSION, 'plan_model': model,
            'solutions': solutions, 'failures': failures, 'judge_model': judge_model,
            'judgement': judgement,
            'judge_error': judge_error, 'created_at': _now()}


def run_job(con, job_id):
    _rows(con)
    job = con.execute('SELECT * FROM answer_jobs WHERE id=?', (job_id,)).fetchone()
    if not job or job['status'] != 'running':
        return
    snapshot = json.loads(job['snapshot'])
    try:
        result = evaluate(snapshot, lambda phase: _phase(con, job_id, phase))
        con.execute('BEGIN IMMEDIATE')
        try:
            active = con.execute('SELECT status FROM answer_jobs WHERE id=?', (job_id,)).fetchone()
            if not active or active[0] != 'running':
                return
            row = con.execute('SELECT * FROM questions WHERE id=?', (job['question_id'],)).fetchone()
            if not row or stem_hash(row) != snapshot['stem_hash'] or _token(con, row) != snapshot['revision']:
                con.execute("UPDATE answer_jobs SET status='stale',phase='stale',result=?,finished_at=? WHERE id=?", (_json(result), _now(), job_id))
            else:
                _write_record(con, row, result)
                con.execute("UPDATE answer_jobs SET status='done',phase='done',result=?,finished_at=? WHERE id=?", (_json(result), _now(), job_id))
            con.commit()
        finally:
            if con.in_transaction:
                con.rollback()
    except InterruptedError:
        pass
    except Exception as exc:
        con.rollback()
        con.execute("UPDATE answer_jobs SET status='error',phase='error',error=?,finished_at=? WHERE id=? AND status='running'", (ai._safe_text(exc), _now(), job_id))
        con.commit()


def _connect():
    con = sqlite3.connect(banklib.DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA busy_timeout=30000')
    return con


def _claim(con):
    _rows(con)
    con.execute('BEGIN IMMEDIATE')
    row = con.execute("SELECT id FROM answer_jobs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
    if row:
        con.execute("UPDATE answer_jobs SET status='running' WHERE id=?", (row[0],))
    con.commit()
    return row[0] if row else None


def _loop():
    con = _connect()
    try:
        while True:
            try:
                job_id = _claim(con)
                if job_id:
                    run_job(con, job_id)
                else:
                    time.sleep(.7)
            except sqlite3.OperationalError:
                con.rollback()
                time.sleep(1)
    finally:
        con.close()


def start_worker():
    global _started
    if os.environ.get('CHEM_DISABLE_AI') == '1':
        return
    with _start_lock:
        if _started:
            return
        ai._load_provider_config()
        con = _connect()
        ensure_schema(con)
        # A restarted application resumes interrupted jobs from an immutable snapshot.
        con.execute("UPDATE answer_jobs SET status='queued',phase='queued' WHERE status='running'")
        con.commit()
        con.close()
        _started = True
        # Conservative concurrency: each question needs five independent requests.
        for _ in range(2):
            threading.Thread(target=_loop, name='ai-answer-worker', daemon=True).start()
