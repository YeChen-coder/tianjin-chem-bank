"""Conservative boundary extraction. Ambiguous answers never vanish."""
import re
from .cleanup import BARE_AFTER
from .importer import block_text, inline_text, slice_paragraph, walk_nodes

QUESTION = re.compile(r'^\s*(?:第\s*)?(\d{1,3})(?:\s*题\s*[:：.．、]?|\s*[.．、:：)）](?:(?!\d)|(?=\d{4}年))|(?=\s*[（(]\s*\d+(?:\.\d+)?\s*分)|'+BARE_AFTER+r')\s*')
SECTION = re.compile(r'^\s*[一二三四五六七八九十]+[、.．].{0,80}')
ANSWERS = re.compile(r'^(?:.{0,90}?(?:试卷|试题|化学|考试|学年).{0,30}?)?(?:参考答案(?:与解析|及解析|及评分标准)?|答案(?:与|和|及)解析|试题解析|答案解析|答案)\s*[:：]?\s*$')
MARKER = re.compile(r'【(答案|解析|详解)】|\[(答案|解析|详解)\]|(?:^|\n)(答案|解析)\s*[:：]')
OPTION = re.compile(r'(?:^|(?<=\s))([A-HＡ-Ｈ])[.．、:：)）]\s*')
SUB = re.compile(r'[（(](\d{1,2})[）)]\s*|([①②③④⑤⑥⑦⑧⑨⑩])\s*|(?:^|(?<=\s))([a-h])[)）]\s*')
ROMAN = re.compile(r'^\s*([ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]|[IVX]{1,5})[.．、]\s*')

def qtype(section):
    for word, kind in [('不定项','多选题'), ('多项','多选题'), ('单项','单选题'), ('选择','选择题'),
                       ('填空','填空题'), ('实验','实验题'), ('计算','计算题'), ('简答','简答题')]:
        if word in section:
            return kind
    return '未知'

def split_paper(blocks, reader):
    questions, retained, boundaries = [], [], []
    section, current, destination, answer_mode, answer_target = '', None, None, False, None
    current_sub = None
    current_group = None
    answer_kind = 'answers'

    def retain(block, reason):
        retained.append({'reason':reason, 'block':block})
        reader.issue('ANSWER_MAPPING_REVIEW' if '答案' in reason else 'SPLIT_REVIEW', reason, block['id'],
                     'review' if '答案' in reason else 'info')

    def new_question(number, block):
        qid = reader.sid('question', block['id'] + ':' + number)
        question = {'id':qid, 'revision':0, 'number':number, 'qtype':qtype(section),
                    'body':[], 'options':[], 'subquestions':[], 'answers':[], 'explanations':[],
                    'source':{'source_sha256':reader.sha, 'number':number,
                              'locator':reader.records.get(reader.sid('source', ''), {}).get('locator', block['id']),
                              'block_id':block['id']}, 'origin':'import', 'issues':[]}
        # Resolve paragraph ID to original XPath, never fabricate a source path.
        for record in reader.records.values():
            if reader.sid('paragraph', record['locator']) == block['id']:
                question['source']['locator'] = record['locator']
                break
        questions.append(question)
        boundaries.append({'question_id':qid, 'number':number, 'source_block_id':block['id'],
                           'source_locator':question['source']['locator'], 'section':section,
                           'ownership':[]})
        return question

    def own(question, block, role, target=None):
        if question is not None:
            boundary = next(x for x in boundaries if x['question_id']==question['id'])
            boundary['ownership'].append({'block_id':block['id'], 'role':role, 'target_id':target or question['id'],
                                           'text':block_text(block)[:200],
                                           'media_ids':[n['asset_id'] for n in walk_nodes(block) if n['type']=='image']})

    def add_answer(question, block, kind='answers'):
        if question is None:
            retain(block, '答案题号无法唯一对应，保留待核对。')
            return
        if block['type'] != 'paragraph':
            entries = question[kind]
            if not entries:
                entries.append({'id':reader.sid('answer', question['id'] + block['id'] + kind),
                                'target_id':question['id'], 'blocks':[]})
            entries[-1]['blocks'].append(block)
            own(question, block, kind, entries[-1]['target_id'])
            return
        text = inline_text(block['inlines'])
        matches = list(SUB.finditer(text))
        if not matches:
            entries = question[kind]
            if not entries:
                entries.append({'id':reader.sid('answer', question['id'] + block['id'] + kind),
                                'target_id':question['id'], 'blocks':[]})
            entries[-1]['blocks'].append(block)
            own(question, block, kind, entries[-1]['target_id'])
            return
        if text[:matches[0].start()].strip():
            add_answer(question, slice_paragraph(block, 0, matches[0].start(), reader), kind)
        for i, match in enumerate(matches):
            label = match[1] or match[2] or match[3]
            stop = matches[i+1].start() if i+1<len(matches) else len(text)
            part = slice_paragraph(block, match.end(), stop, reader)
            targets = [s for s in _subnodes(question['subquestions']) if s['label']==label]
            if len(targets) != 1:
                original = slice_paragraph(block, match.start(), stop, reader)
                if not match[1]:
                    # A circle often enumerates several blanks within the last
                    # explicitly matched numeric ask. Keep the label and image
                    # there instead of attempting a non-existent target ID.
                    if not question[kind]:
                        question[kind].append({'id':reader.sid('answer',original['id']+kind),
                                               'target_id':question['id'], 'blocks':[]})
                    question[kind][-1]['blocks'].append(original)
                    own(question, original, kind, question[kind][-1]['target_id'])
                else:
                    ref = reader.sid('source', original['id'])
                    reader.records[ref] = {'kind':'unmapped_answer', 'source_sha256':reader.sha,
                                           'locator':original['id'], 'blocks':[original]}
                    unresolved = {'id':reader.sid('unresolved',original['id']), 'type':'unresolved',
                                  'source_ref':ref, 'reason':'答案小问标记无法唯一匹配：' + label,
                                  'fallback_blocks':[original]}
                    question[kind].append({'id':reader.sid('answer',unresolved['id']+kind),
                                           'target_id':question['id'], 'blocks':[unresolved]})
                    reader.issue('ANSWER_MAPPING_REVIEW', unresolved['reason'], unresolved['id'])
                    own(question, unresolved, 'unmapped_answer')
                continue
            entry = {'id':reader.sid('answer', part['id'] + kind), 'target_id':targets[0]['id'], 'blocks':[part]}
            question[kind].append(entry)
            own(question, part, kind, entry['target_id'])

    def add_content(block):
        nonlocal destination, current_sub, current_group
        if current is None:
            retain(block, '卷首说明/未归属正文，按原顺序保留。')
            return
        if block['type'] != 'paragraph':
            (destination if destination is not None else current['body']).append(block)
            own(current, block, 'body/table' if destination is None else 'continuation')
            return
        text = inline_text(block['inlines'])
        roman = ROMAN.match(text)
        if roman:
            group = {'id':reader.sid('sub', block['id'] + '/roman'), 'label':roman[1],
                     'blocks':[slice_paragraph(block, roman.end(), len(text), reader)], 'subquestions':[]}
            current['subquestions'].append(group)
            current_group, current_sub, destination = group, None, group['blocks']
            own(current, group['blocks'][0], 'subsection', group['id'])
            return
        # Options need strong delimiters. Subquestion parenthesis is explicit.
        # Circled items commonly list reagents or experiment steps. Keep those
        # in content; only numeric parentheses and explicit nested a) delimiters
        # establish subquestion groups. Options inside a subquestion stay there.
        option_marks = [] if current_sub is not None else [('option', m) for m in OPTION.finditer(text)]
        sub_marks = [('sub', m) for m in SUB.finditer(text)
                     if m[1] or (m[3] and current_sub is not None and not text[:m.start()].strip())]
        if sub_marks:
            option_marks = [x for x in option_marks if x[1].start() < sub_marks[0][1].start()]
        marks = option_marks + sub_marks
        marks.sort(key=lambda x:x[1].start())
        if not marks:
            (destination if destination is not None else current['body']).append(block)
            own(current, block, 'continuation' if destination is not None else 'body')
            return
        if marks[0][1].start() > 0:
            prefix = slice_paragraph(block, 0, marks[0][1].start(), reader)
            if prefix['inlines']:
                (destination if destination is not None else current['body']).append(prefix)
                own(current, prefix, 'prefix')
        for i, (kind, match) in enumerate(marks):
            stop = marks[i+1][1].start() if i+1<len(marks) else len(text)
            part = slice_paragraph(block, match.end(), stop, reader)
            label = match[1] if kind=='option' else match[1] or match[2] or match[3]
            item = {'id':reader.sid(kind, block['id'] + str(match.start())), 'label':label, 'blocks':[part]}
            if kind=='option':
                current['options'].append(item)
                destination = item['blocks']
            else:
                if match[1]:
                    siblings = current_group['subquestions'] if current_group is not None else current['subquestions']
                    if any(s['label']==label for s in siblings):
                        # Teacher exports may repeat labels in an unmarked
                        # explanatory paragraph. Preserve it in source order,
                        # flag its role, and do not create duplicate small asks.
                        original = slice_paragraph(block, match.start(), stop, reader)
                        (destination if isinstance(destination,list) else current['body']).append(original)
                        reader.issue('ANSWER_MAPPING_REVIEW', '题内重复小问标记 ' + label + '，可能是未标记解析；原文保留，需核对角色。', original['id'])
                        own(current, original, 'unclassified_repeated_label')
                        continue
                    siblings.append(item)
                    current_sub = item
                elif current_sub is not None:
                    current_sub.setdefault('subquestions', []).append(item)
                else:
                    current['subquestions'].append(item)
                destination = item['blocks']
            own(current, part, kind, item['id'])

    def process(block):
        nonlocal section, current, destination, current_sub, current_group, answer_mode, answer_target, answer_kind
        if block['type'] != 'paragraph':
            if block['type']=='table' and len(block['rows'])>=2:
                first=block['rows'][0]['cells'];second=block['rows'][1]['cells']
                label=lambda c:block_text({'type':'table','rows':[{'cells':[c]}]}).strip()
                if first and second and label(first[0]) in {'题号','题目'} and label(second[0]) in {'答案','参考答案'} and _answer_table(block,questions,reader,add_answer):
                    return
            if answer_mode:
                # A number/answer table can be mapped only when each row is explicit.
                if block['type']=='table' and _answer_table(block, questions, reader, add_answer):
                    return
                add_answer(answer_target, block, answer_kind)
            elif destination == 'ANSWER':
                add_answer(current, block, answer_kind)
            else:
                add_content(block)
            return
        text = inline_text(block['inlines'])
        if ANSWERS.fullmatch(text.strip()):
            retained.append({'reason':'参考答案区域标题', 'block':block})
            current, destination, current_sub, current_group = None, None, None, None
            answer_mode, answer_target, answer_kind = True, None, 'answers'
            return
        if SECTION.match(text):
            section = text.strip()
            retained.append({'reason':'大题栏目', 'block':block})
            current, destination, current_sub, current_group = None, None, None, None
            if not answer_mode:
                answer_target = None
            return
        numbered = QUESTION.match(text)
        if numbered:
            number = numbered[1]
            if answer_mode:
                targets = [q for q in questions if q['number']==number]
                answer_target = targets[0] if len(targets)==1 else None
                answer_kind = 'answers'
                if answer_target is None:
                    retain(block, '答案题号重复或缺失，无法唯一匹配：' + number)
                    return
                block = slice_paragraph(block, numbered.end(), len(text), reader)
            elif current is not None and int(number) <= int(current['number']) and section:
                reader.issue('SPLIT_REVIEW', '题内重复/较小数字编号保留为原题内容，需核对边界。', current['id'])
            else:
                current = new_question(number, block)
                destination, current_sub, current_group = None, None, None
                block = slice_paragraph(block, numbered.end(), len(text), reader)
        elif current is None and not answer_mode:
            retain(block, '卷首说明/未归属正文，按原顺序保留。')
            return
        # Inline teacher edition answers keep exact content on each side.
        text = inline_text(block['inlines'])
        markers = list(MARKER.finditer(text))
        if markers:
            if markers[0].start():
                prefix = slice_paragraph(block, 0, markers[0].start(), reader)
                if answer_mode:
                    add_answer(answer_target, prefix, answer_kind)
                elif destination == 'ANSWER':
                    add_answer(current, prefix, answer_kind)
                else:
                    add_content(prefix)
            for i, marker in enumerate(markers):
                stop = markers[i+1].start() if i+1<len(markers) else len(text)
                label = next(x for x in marker.groups() if x)
                kind = 'answers' if label=='答案' else 'explanations'
                part = slice_paragraph(block, marker.end(), stop, reader)
                add_answer(answer_target if answer_mode else current, part, kind)
                answer_kind = kind
            if not answer_mode:
                destination = 'ANSWER'
        elif answer_mode:
            add_answer(answer_target, block, answer_kind)
        elif destination == 'ANSWER':
            add_answer(current, block, answer_kind)
        else:
            add_content(block)

    for block in blocks:
        # Only unwrap an unmerged layout table when every nonempty cell starts
        # with an independent question. Data/option tables remain native tables.
        if block['type']=='table' and not answer_mode:
            cells = [c for r in block['rows'] for c in r['cells'] if any(block_text(b).strip() for b in c['blocks'])]
            if cells and all(c['row_span']==c['col_span']==1 and c['blocks'] and c['blocks'][0]['type']=='paragraph'
                             and QUESTION.match(block_text(c['blocks'][0])) for c in cells):
                retained.append({'reason':'原题目布局表格（内容已逐格分题）', 'block':block})
                reader.issue('SPLIT_REVIEW', '题目布局表格按单元格分题，原表格保留供核对。', block['id'])
                for cell in cells:
                    for part in cell['blocks']:
                        process(part)
                continue
        process(block)
    if not questions:
        reader.issue('SPLIT_REVIEW', '未识别独立题号，完整原文保留，不能视为成功分题。', severity='error')
    return questions, retained, boundaries

def _subnodes(nodes):
    for node in nodes:
        yield node
        yield from _subnodes(node.get('subquestions', []))

def _answer_table(table, questions, reader, add_answer):
    # Horizontal compact key: first row question numbers, second row answers.
    rows = table['rows']
    if not rows or len(rows)%2:
        return False
    pairs = []
    for r in range(0,len(rows),2):
        if len(rows[r]['cells'])!=len(rows[r+1]['cells']):return False
        labels=[block_text({'type':'table','rows':[{'cells':[c]}]}).strip() for c in rows[r]['cells']]
        for i,label in enumerate(labels):
            if label in {'题号','题目','答案'}:continue
            answer_blocks=rows[r+1]['cells'][i]['blocks']
            if not label and not any(block_text(b).strip() for b in answer_blocks):continue
            if not re.fullmatch(r'\d{1,3}',label):return False
            targets=[q for q in questions if q['number']==label]
            if len(targets)!=1:return False
            pairs.append((targets[0],answer_blocks))
    if not pairs:
        return False
    for question, blocks in pairs:
        for block in blocks:
            add_answer(question, block)
    reader.record_table_mapping = table['id']
    return True
