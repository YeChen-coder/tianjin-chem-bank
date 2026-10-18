"""Find review candidates without changing question content or guessing answers."""
import hashlib
import json
import re
from .inline_model import txt, slice_nodes
from .text_formula import convert_text_span

# A lexer finds spans; the chemistry parser validates complete candidates.
# Existing equations, images and Chinese prose stop a text span.
SPAN = re.compile(r'[A-Za-zΑ-Ωα-ω0-9₀-₉⁰¹²³⁴⁵⁶⁷⁸⁹(\[{][A-Za-zΑ-Ωα-ω0-9₀-₉⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻+−\-*/=<>≥≤≠→←↔⇌⇄·⋅.()\[\]{}↑↓ \t]*')
RELATION = re.compile(r'[=<>≥≤≠→←↔⇌⇄]')


def paragraphs(blocks):
    for block in blocks:
        if block['kind'] == 'paragraph':
            yield block
        else:
            for row in block.get('rows',[]):
                for cell in row:
                    yield from paragraphs(cell['blocks'])


def source_key(nodes):
    # UI assigns IDs while rendering. Those IDs are not document content.
    data=[{k:v for k,v in n.items() if k!='id'} for n in nodes]
    return hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def review_candidate_id(model,question,scope,block,kind,source,start=None,end=None):
    stable=[model['source_sha256'],question['id'],scope,block['id'],kind,source,start,end]
    return hashlib.sha256(json.dumps(stable,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()[:24]


def text_candidates(block):
    text=txt(block['inlines'])
    # Full stops/option labels are boundaries; keep decimal points.
    masked=''.join(c if c!='.' or (i>0 and i+1<len(text) and text[i-1].isdigit() and text[i+1].isdigit()) else '\ufffc' for i,c in enumerate(text))
    masked=re.sub(r'[ \t]{2,}(?=[A-D]\ufffc)',lambda m:'\ufffc'*len(m[0]),masked)
    for match in SPAN.finditer(masked):
        start,end=match.span()
        if block.get('role')=='subquestion' and not text[:start].strip():
            label=re.match(r'\(\d{1,3}\)\s*',text[start:end])
            if label:start+=len(label[0])
        while start<end and text[start].isspace():start+=1
        while end>start and text[end-1].isspace():end-=1
        # Closing prose parentheses can border a formula; leave them outside
        # the replacement. Never invent a missing internal delimiter.
        while end>start and text[end-1] in ')]}':
            closing=text[end-1];opening={')':'(',']':'[','}':'{'}[closing]
            chunk=text[start:end]
            if chunk.count(closing)<=chunk.count(opening):break
            end-=1
        source=slice_nodes(block['inlines'],start,end)
        value=txt(source)
        if not value or len(value)>300 or any(n['kind'] not in {'text','tab'} for n in source):continue
        stack=[]
        valid=True
        for char in value:
            if char in '([{':stack.append(char)
            elif char in ')]}':
                if not stack or {'(':')','[':']','{':'}'}[stack.pop()]!=char:
                    valid=False;break
        if stack or not valid:continue
        # Do not turn a raw/unmapped legacy symbol into a guessed formula.
        if any(n.get('symbol_normalized') is False for n in source):continue
        scripted=any(n.get('vert') in {'subscript','superscript'} for n in source) or bool(re.search('[₀-₉⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻]',value))
        chemistry_signal=scripted or bool(re.search(r'[0-9+·⋅→←↔⇌⇄]|pH\s*[<>=≥≤]',value))
        candidate=None
        if chemistry_signal:
            try:candidate=convert_text_span({'block':block,'start':start,'end':end,'mode':'chemistry'})
            except ValueError:pass
        mode='chemistry'
        if candidate is None:
            # Plain chemical names/units and variable labels alone are not
            # evidence of a mathematical formula.
            if not scripted and not RELATION.search(value):continue
            if not re.search('[A-Za-zΑ-Ωα-ω]',value):continue
            mode='mathematics'
            candidate=convert_text_span({'block':block,'start':start,'end':end,'mode':mode})
        yield {'start':start,'end':end,'mode':mode,'source_text':value,'source_parts':source,'source_key':source_key(source),'latex':candidate['latex']}


def scan_model(data):
    model=data['model']
    items=[]
    ignored=model.get('review_decisions',{})
    def add(question,scope,block,kind,source,**details):
        item_id=review_candidate_id(model,question,scope,block,kind,source,details.get('start'),details.get('end'))
        if ignored.get(item_id,{}).get('status')=='kept':return
        items.append({'id':item_id,'question_id':question['id'],'question_number':question.get('number',''),'scope':scope,'block_id':block['id'],'kind':kind,**details})
    for question in model['questions']:
        for scope in ('blocks','answers'):
            for block in paragraphs(question.get(scope,[])):
                for candidate in text_candidates(block):
                    add(question,scope,block,'text_formula',candidate['source_key'],reason='文字中可能含有公式，可转换并对照核对。',**candidate)
                for index,node in enumerate(block['inlines']):
                    key=source_key([node])
                    common={'node_index':index,'source_key':key}
                    if node.get('field_warning'):
                        if index and node.get('source_field_id') and block['inlines'][index-1].get('source_field_id')==node['source_field_id']:continue
                        add(question,scope,block,'unresolved',key,reason=node['field_warning'],source_field_code=node.get('source_field_code',''),**common)
                    elif node['kind']=='image':
                        add(question,scope,block,'image',key,reason='确认这是公式图片还是题目配图，并检查摆放位置。',asset=node['asset'],**common)
                    elif node['kind']=='math' and (node.get('needs_confirmation') or node.get('reader_warning') or node.get('editor_warning')):
                        add(question,scope,block,'math_review',key,reason=node.get('normalization_note') or node.get('editor_warning') or node.get('reader_warning'),**common)
                    elif node['kind']=='missing' or node.get('symbol_normalized') is False:
                        add(question,scope,block,'unresolved',key,reason=node.get('message','字体符号尚未映射，需要对照原文件补齐。'),**common)
                    elif node['kind']=='text' and node.get('vert') in {'subscript','superscript'} and index>0 and block['inlines'][index-1]['kind'] in {'image','math'}:
                        add(question,scope,block,'unresolved',key,reason='上下标与前面的图片或公式分开，需要合并后核对。',**common)
    counts={kind:sum(item['kind']==kind for item in items) for kind in ('text_formula','image','math_review','unresolved')}
    return {'items':items,'counts':counts,'warnings':model.get('warnings',[]),'note':'这是候选清单，文字公式可能有漏检或误检；核对后再应用。扫描本身不会修改题目。'}
