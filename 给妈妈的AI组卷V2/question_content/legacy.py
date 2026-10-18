"""Explicit legacy bridge. Retains original payload; refuses lossy writes."""
from copy import deepcopy
import hashlib
import json
from .storage import ContentError
from . import compile_formula,validate_question


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def question_from_legacy(row,store,asset_register=None,metadata=None):
    """Read an already loaded legacy row. No database migration or legacy writes.

    asset_register(part)->Asset must be a trusted media resolver; paths never
    come from the client. All legacy metadata is linked verbatim in private storage.
    """
    row=dict(row);qid='legacy-'+str(row['id']);seq=0;assets={};issues=[]
    def id_(kind):
        nonlocal seq
        seq+=1;return qid+'-'+kind+'-'+str(seq)
    def text(value,style=None):
        n={'id':id_('text'),'type':'text','text':value}
        if style:n['style']=style
        return n
    def paragraph(inlines):return {'id':id_('paragraph'),'type':'paragraph','align':'left','inlines':inlines}
    def unresolved(original,reason):
        ref=id_('source');store.register_source(ref,{'legacy':original})
        node={'id':id_('unresolved'),'type':'unresolved','source_ref':ref,'reason':reason,'fallback_blocks':[paragraph([text(str(original.get('s','')) if isinstance(original,dict) else json.dumps(original,ensure_ascii=False))])]}
        issues.append({'id':id_('issue'),'code':'UNSUPPORTED_SOURCE','severity':'review','question_id':qid,'node_id':node['id'],'message':reason})
        return node
    def parts(items):
        result=[]
        for part in items:
            if not isinstance(part,dict) or part.get('t') not in {'text','img'}:
                return None
            if part['t']=='img':
                if not asset_register:return None
                a=asset_register(part);assets[a['id']]=a
                result.append({'id':id_('image'),'type':'image','asset_id':a['id'],'width_mm':float(part.get('width_mm') or 40),'height_mm':float(part.get('height_mm') or 30)})
            elif part.get('latex') or part.get('omml'):
                ref=id_('source');latex=part.get('latex')
                if latex is None and part.get('omml'):
                    try:
                        from formula_engine.engine import native_formula
                        latex=native_formula(part['omml'])['latex']
                    except Exception:pass
                store.register_source(ref,{'legacy':part,'latex':latex,'omml':part.get('omml'),'text':part.get('s','')})
                result.append({'id':id_('formula'),'type':'formula','latex':latex,'status':'restored' if latex else 'review_required','source_ref':ref})
            else:
                style={k:part[k] for k in ('bold','italic') if k in part}
                if part.get('vert') in {'subscript','superscript'}:style['vertical']=part['vert']
                result.append(text(part.get('s',''),style or None))
        return result
    original=row.get('segments') or []
    if isinstance(original,str):original=json.loads(original)
    body=[]
    for block in original:
        if isinstance(block,list):
            inlines=parts(block)
            body.append(paragraph(inlines) if inlines is not None else unresolved(block,'旧内容包含未适配的结构/图片，保留来源'))
        elif isinstance(block,dict) and block.get('t')=='table' and block.get('rows'):
            rows=block['rows'];columns=max(map(len,rows))
            if any(len(r)!=columns for r in rows):body.append(unresolved(block,'旧表格列数/合并信息不完整'));continue
            table={'id':id_('table'),'type':'table','columns_mm':[160/columns]*columns,'rows':[]}
            unsupported=False
            for r in rows:
                cells=[]
                for c,items in enumerate(r):
                    content=parts(items)
                    if content is None:unsupported=True;break
                    cells.append({'id':id_('cell'),'col_index':c,'row_span':1,'col_span':1,'blocks':[paragraph(content)]})
                table['rows'].append({'id':id_('row'),'cells':cells})
            body.append(unresolved(block,'旧表格包含未适配内容') if unsupported else table)
        else:body.append(unresolved(block,'旧块类型未适配'))
    if not body:body=[paragraph([text(row.get('body') or '')])]
    q={'id':qid,'revision':0,'number':str(row.get('qnum') or ''),'qtype':row.get('qtype') or '',
       'body':body,'options':[],'subquestions':[],'answers':[],'explanations':[],
       'source':{'source_sha256':None,'number':str(row.get('qnum') or ''),'locator':'legacy-row-'+str(row['id'])},'origin':'import','issues':issues}
    if row.get('answer'):q['answers']=[{'id':id_('answer'),'target_id':qid,'blocks':[paragraph([text(row['answer'])])]}]
    report=validate_question(q,list(assets.values()))
    if not report['ok']:raise ContentError('LEGACY_INVALID','旧内容适配失败，原内容保留',details=report)
    store.register_source(qid+'-legacy-original',{'row':row,'metadata':metadata or {},'content_sha256':fingerprint({k:q[k] for k in ('body','options','subquestions','answers','explanations')})})
    store.link_legacy(qid,int(row['id']),{'row':row,'metadata':metadata or {}})
    return {'question':q,'assets':list(assets.values()),'report':report}


def legacy_segments_from_question(question,store):
    """Unedited data returns original bytes/shape. Structural edits need v2 storage.

    Returning a misleading flattened version would lose options, target links,
    merges and source preservation. This bridge intentionally refuses that write.
    """
    record=store.resolve_source(question['id']+'-legacy-original')
    if record and record['content_sha256']==fingerprint({k:question[k] for k in ('body','options','subquestions','answers','explanations')}):
        return deepcopy(record['row']['segments'])
    raise ContentError('LEGACY_WRITE_REQUIRES_V2','编辑后的结构化内容必须存入 v2，不能通过旧 segments 写回丢失关联')
