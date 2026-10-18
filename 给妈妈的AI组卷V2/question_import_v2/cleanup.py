"""Remove exam furniture; original numbers remain in source metadata."""
from copy import deepcopy
import re
import unicodedata

STEM_START=r'(?:下列|根据|如图|某|现有|已知|请|回答|化学|实验|物质|在|为|为了|有|将|取|用|关于|我国|生活|学习|同学|我国|科学|图中|运用|写出|完成|选择|判断|观察|分析|推断)'
UNITS=r'(?:种|个|克|毫|千|米|年|月|日|次|组|分钟|小时|秒|吨|升|分子|原子|离子|倍|份|滴)'
BARE_AFTER=rf'(?=\s+(?!{UNITS})[\u4e00-\u9fff“「])|(?={STEM_START})'
SCORE=re.compile(r'^\s*[（(]\s*(?:(?:本题|共|每小题|每空)\s*)?\d+(?:\.\d+)?\s*分(?:\s*[,，;；、]\s*(?:(?:本题|共|每小题|每空)\s*)?\d+(?:\.\d+)?\s*分)*\s*[）)]\s*')

def walk(value):
    if isinstance(value,dict):
        if 'type' in value:yield value
        for child in value.values():yield from walk(child)
    elif isinstance(value,list):
        for child in value:yield from walk(child)

def inline_text(node):
    if node.get('type')=='text':return node['text']
    if node.get('type')=='formula':
        from question_content.bank_metadata import formula_text
        return formula_text(node.get('latex'))
    return '\ufffc'

def trim_prefix(paragraph,length):
    """Keep surviving text IDs/styles; never cut through a native formula."""
    remaining=length;out=[]
    for node in paragraph['inlines']:
        size=len(inline_text(node))
        if remaining>=size:remaining-=size;continue
        if remaining:
            if node['type']=='formula':
                suffix=inline_text(node)[remaining:]
                # Some Word equation objects contain only the printed score,
                # a small-ask label, and punctuation. Recover that literal
                # suffix without rewriting any scientific formula.
                if not re.fullmatch(r'\s*(?:[（(]\d{1,2}[）)])?\s*[“”‘’《》「」:：.．、]*\s*',suffix):return False
                node={'id':node['id'],'type':'text','text':suffix}
            elif node['type']=='text':
                node=deepcopy(node);node['text']=node['text'][remaining:]
            else:return False
            remaining=0
        out.append(node)
    if remaining:return False
    paragraph['inlines']=out;return True

def prefix_text(paragraph):
    text=''
    for node in paragraph['inlines']:
        text+=inline_text(node)
        if len(text)>=128:break
    return text[:128]

def clean_question(question):
    before_ids={n['id'] for n in walk(question) if 'id' in n}
    result={'prefixes_removed':[],'identity_options_removed':[],'removed_ids':[]}
    number=str(question.get('source',{}).get('number') or question.get('number') or '')
    normalized_number=unicodedata.normalize('NFKC',number)
    digits=''.join('['+d+chr(ord(d)+0xFEE0)+']' if d.isascii() and d.isdigit() else re.escape(d) for d in normalized_number)
    number_pattern=re.compile(r'^\s*(?:第\s*)?'+digits+rf'(?:\s*题\s*[:：.．、]?|\s*[.．、:：)）](?!\d)|(?=\s*[（(]\s*\d+(?:\.\d+)?\s*分)|{BARE_AFTER})\s*') if number else None
    first=next((b for b in question['body'] if b['type']=='paragraph' and b['inlines']),None)
    if first:
        for _ in range(4):
            text=prefix_text(first)
            marker=SCORE.match(text) or (number_pattern.match(text) if number_pattern else None)
            if not marker or not trim_prefix(first,marker.end()):break
            result['prefixes_removed'].append(marker[0])
    # Small asks retain their labels, but printed score badges are dispensable.
    for sub in question.get('subquestions',[]):
        first_sub=next((b for b in sub['blocks'] if b['type']=='paragraph' and b['inlines']),None)
        if first_sub:
            text=prefix_text(first_sub);marker=SCORE.match(text)
            if marker and trim_prefix(first_sub,marker.end()):result['prefixes_removed'].append(marker[0])
    options=question.get('options',[])
    labels=[unicodedata.normalize('NFKC',o['label']).strip() for o in options]
    identity=len(options)>=4 and labels==list('ABCDEFGH'[:len(options)])
    for option,label in zip(options,labels):
        text='';valid=True
        for block in option['blocks']:
            if block['type']!='paragraph' or any(n['type']!='text' for n in block['inlines']):valid=False;break
            text+=''.join(n['text'] for n in block['inlines'])
        if not valid or unicodedata.normalize('NFKC',text).strip()!=label:identity=False;break
    represented=False
    if identity:
        content=list(walk({'body':question['body'],'subquestions':question.get('subquestions',[])}))
        displayed_labels=set()
        for block in content:
            if block['type']!='paragraph':continue
            text=unicodedata.normalize('NFKC',prefix_text(block)).strip()
            marker=re.match(r'^([A-H])(?:[.、:)]|\s|$|(?=[\u4e00-\u9fff]))',text)
            if marker:displayed_labels.add(marker[1])
        represented=any(n['type']=='image' for n in content) or set(labels).issubset(displayed_labels)
    if identity and represented:
        removed={o['id'] for o in options};result['identity_options_removed']=labels;question['options']=[]
        for part in question.get('answers',[])+question.get('explanations',[]):
            if part['target_id'] in removed:part['target_id']=question['id']
    after_ids={n['id'] for n in walk(question) if 'id' in n}
    result['removed_ids']=sorted(before_ids-after_ids)
    question['issues']=[i for i in question.get('issues',[]) if i.get('node_id') not in result['removed_ids']]
    return result
