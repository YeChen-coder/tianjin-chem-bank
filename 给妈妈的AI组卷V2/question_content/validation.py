"""Non-mutating schema-v1 validation. Reports structure, not scientific truth."""
import math
import re
import hashlib


def validate_question(question, assets=None):
    return _Validator(assets).question_report(question)


def validate_bundle(bundle):
    v = _Validator([])
    if not v.obj(bundle, '$', {'schema_version', 'paper', 'questions', 'assets', 'issues'}):
        return v.report()
    if type(bundle.get('schema_version')) is not int or bundle['schema_version'] != 1:
        v.error('$.schema_version', 'SCHEMA_VERSION', 'schema_version 必须为整数 1')
    assets = bundle.get('assets')
    if v.array(assets, '$.assets'):
        v.assets = set()
        for i, a in enumerate(assets):
            p = f'$.assets[{i}]'
            if not v.obj(a, p, {'id', 'sha256', 'mime_type', 'preview_url'}): continue
            v.identity(a, p)
            if isinstance(a.get('id'), str): v.assets.add(a['id'])
            if not isinstance(a.get('sha256'), str) or not re.fullmatch('[0-9a-fA-F]{64}', a['sha256']):
                v.error(p+'.sha256', 'ASSET_HASH', '资产摘要必须为 SHA256')
            v.string(a.get('mime_type'), p+'.mime_type')
            v.string(a.get('preview_url'), p+'.preview_url')
            if isinstance(a.get('preview_url'), str) and not a['preview_url'].startswith('/api/v2/content/assets/'):
                v.error(p+'.preview_url', 'ASSET_URL', '资产 URL 必须由本机资产服务生成')
    paper = bundle.get('paper')
    if v.obj(paper, '$.paper', {'id', 'title', 'question_ids', 'source_sha256','annotations'}, {'annotations'}):
        v.identity(paper, '$.paper'); v.string(paper.get('title'), '$.paper.title')
        v.sha(paper.get('source_sha256'), '$.paper.source_sha256')
        if 'annotations' in paper and v.array(paper['annotations'],'$.paper.annotations'):
            for i,a in enumerate(paper['annotations']):
                ap=f'$.paper.annotations[{i}]'
                if v.obj(a,ap,{'before_question_id','blocks'}):
                    v.string(a.get('before_question_id'),ap+'.before_question_id',True)
                    if a.get('before_question_id') is not None and a['before_question_id'] not in paper.get('question_ids',[]):v.error(ap,'QUESTION_ORDER','卷面说明的题目锚点不存在')
                    v.blocks(a.get('blocks'),ap+'.blocks',set())
    questions = bundle.get('questions')
    qids = []
    if v.array(questions, '$.questions'):
        for i, q in enumerate(questions):
            v.question(q, f'$.questions[{i}]')
            if isinstance(q, dict): qids.append(q.get('id'))
    if isinstance(paper, dict):
        ordered = paper.get('question_ids')
        if v.array(ordered, '$.paper.question_ids'):
            if any(not isinstance(x, str) for x in ordered) or len(set(map(str, ordered))) != len(ordered) or set(map(str, ordered)) != set(map(str, qids)):
                v.error('$.paper.question_ids', 'QUESTION_ORDER', '题目引用必须无重复并完整对应 questions')
    v.issues(bundle.get('issues'), '$.issues')
    v.check_issues()
    return v.report()


class _Validator:
    def __init__(self, assets):
        self.errors = []
        self.ids = set()
        self.issue_ids = {}
        self.issue_refs = []
        self.question_ids = set()
        self.assets = None if assets is None else {a['id'] if isinstance(a, dict) else a for a in assets}

    def report(self):
        issues=[{'id':'validation-'+hashlib.sha256((e['path']+e['code']+e['message']).encode()).hexdigest()[:24],
                 'code':e['code'],'severity':'error','question_id':None,'node_id':None,
                 'message':e['path']+'：'+e['message']} for e in self.errors]
        return {'ok': not self.errors, 'errors': self.errors, 'issues':issues, 'warnings': []}

    def error(self, path, code, message): self.errors.append({'path': path, 'code': code, 'message': message})

    def obj(self, obj, path, fields, optional=()):
        if not isinstance(obj, dict):
            self.error(path, 'TYPE', '必须为对象'); return False
        for field in fields-set(optional):
            if field not in obj: self.error(path+'.'+field, 'REQUIRED', '缺少必要字段')
        for field in obj.keys()-fields:
            self.error(path+'.'+field, 'UNKNOWN_FIELD', '不接受未约定字段，不会静默删除')
        return True

    def array(self, obj, path):
        if not isinstance(obj, list): self.error(path, 'TYPE', '必须为数组'); return False
        return True

    def string(self, value, path, nullable=False):
        if nullable and value is None: return
        if not isinstance(value, str) or not value:
            self.error(path, 'TYPE', '必须为非空字符串')
        elif any(ord(c) < 32 and c not in '\n\r\t' for c in value):
            self.error(path, 'XML_CHARACTER', '包含 Word 不允许的控制字符')

    def identity(self, obj, path):
        value = obj.get('id'); self.string(value, path+'.id')
        if isinstance(value, str):
            if value in self.ids: self.error(path+'.id', 'DUPLICATE_ID', '文档内 ID 重复')
            self.ids.add(value)

    def sha(self, value, path):
        if value is not None and (not isinstance(value, str) or not re.fullmatch('[0-9a-fA-F]{64}', value)):
            self.error(path, 'SOURCE_HASH', '来源摘要应为 SHA256 或 null')

    def choice(self, obj, key, values, path):
        if not isinstance(obj.get(key),str) or obj[key] not in values: self.error(path+'.'+key, 'ENUM', '字段值不合法')

    def positive(self, value, path, integer=False):
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0 or (integer and type(value) is not int):
            self.error(path, 'DIMENSION', '必须为正整数' if integer else '必须为有限正数')

    def question_report(self, q):
        self.question(q, '$'); self.check_issues(); return self.report()

    def question(self, q, p):
        if not self.obj(q, p, {'id','revision','number','qtype','body','options','subquestions','answers','explanations','source','origin','issues'}): return
        self.identity(q,p)
        if isinstance(q.get('id'),str): self.question_ids.add(q['id'])
        if type(q.get('revision')) is not int or q['revision'] < 0: self.error(p+'.revision','REVISION','revision 必须为非负整数')
        for field in ('number','qtype'):
            if not isinstance(q.get(field),str): self.error(p+'.'+field,'TYPE','必须为字符串，未知可以为空')
        self.choice(q,'origin',{'import','teacher','ai'},p)
        if not isinstance(q.get('source'),dict): self.error(p+'.source','TYPE','source 必须为对象')
        elif 'source_sha256' in q['source']: self.sha(q['source']['source_sha256'],p+'.source.source_sha256')
        targets = {q.get('id')} if isinstance(q.get('id'), str) else set()
        self.blocks(q.get('body'),p+'.body',targets)
        def groups(items, path, nested=False):
            if not self.array(items,path): return
            for i,g in enumerate(items):
                gp=f'{path}[{i}]'
                if not self.obj(g,gp,{'id','label','blocks'} | ({'subquestions'} if nested else set()), {'subquestions'} if nested else set()): continue
                self.identity(g,gp); self.string(g.get('label'),gp+'.label')
                if nested and isinstance(g.get('id'),str): targets.add(g['id'])
                self.blocks(g.get('blocks'),gp+'.blocks',targets)
                if nested and 'subquestions' in g: groups(g['subquestions'],gp+'.subquestions',True)
        groups(q.get('options'),p+'.options'); groups(q.get('subquestions'),p+'.subquestions',True)
        for field in ('answers','explanations'):
            if not self.array(q.get(field),p+'.'+field): continue
            for i,a in enumerate(q[field]):
                ap=f'{p}.{field}[{i}]'
                if not self.obj(a,ap,{'id','target_id','blocks'}): continue
                self.identity(a,ap)
                if not isinstance(a.get('target_id'),str) or a['target_id'] not in targets: self.error(ap+'.target_id','ANSWER_MAPPING_REVIEW','答案目标必须属于同一道题、其小问或作答空')
                self.blocks(a.get('blocks'),ap+'.blocks',set())
        self.issues(q.get('issues'),p+'.issues')

    def image(self, node, p):
        if not isinstance(node.get('asset_id'),str) or (self.assets is not None and node['asset_id'] not in self.assets): self.error(p+'.asset_id','ASSET_MISSING','图片必须引用已登记资产')
        for key in ('width_mm','height_mm'): self.positive(node.get(key),p+'.'+key)

    def blocks(self, blocks, p, targets):
        if not self.array(blocks,p): return
        for i,b in enumerate(blocks):
            bp=f'{p}[{i}]'
            if not isinstance(b,dict): self.error(bp,'TYPE','块必须为对象'); continue
            kind=b.get('type')
            fields={'paragraph':{'inlines','align'},'image':{'asset_id','width_mm','height_mm','align'},'unresolved':{'source_ref','reason','fallback_blocks'},'table':{'columns_mm','rows'}}
            if not isinstance(kind,str) or kind not in fields: self.error(bp,'BLOCK_TYPE','不支持的块类型'); continue
            self.obj(b,bp,{'id','type'}|fields[kind],{'align'} if kind in {'paragraph','image'} else set()); self.identity(b,bp)
            if 'align' in b: self.choice(b,'align',{'left','center','right'},bp)
            if kind=='image': self.image(b,bp)
            elif kind=='unresolved':
                self.string(b.get('source_ref'),bp+'.source_ref'); self.string(b.get('reason'),bp+'.reason'); self.blocks(b.get('fallback_blocks'),bp+'.fallback_blocks',targets)
                if not b.get('fallback_blocks'): self.error(bp,'UNRESOLVED_EMPTY','未恢复内容必须保留可显示的 fallback')
            elif kind=='table': self.table(b,bp,targets)
            elif self.array(b.get('inlines'),bp+'.inlines'):
                for j,n in enumerate(b['inlines']):
                    np=f'{bp}.inlines[{j}]'
                    if not isinstance(n,dict): self.error(np,'TYPE','行内节点必须为对象'); continue
                    k=n.get('type'); fs={'text':{'text','style'},'formula':{'latex','status','source_ref'},'image':{'asset_id','width_mm','height_mm'},'blank':{'width_mm'}}
                    if not isinstance(k,str) or k not in fs: self.error(np,'INLINE_TYPE','不支持的行内类型'); continue
                    self.obj(n,np,{'id','type'}|fs[k],{'style'} if k=='text' else set()); self.identity(n,np)
                    if k=='text':
                        if not isinstance(n.get('text'),str): self.error(np+'.text','TYPE','文字必须为字符串')
                        elif n['text']: self.string(n['text'],np+'.text')
                        if 'style' in n and self.obj(n['style'],np+'.style',{'bold','italic','vertical'},{'bold','italic','vertical'}):
                            for flag in ('bold','italic'):
                                if flag in n['style'] and type(n['style'][flag]) is not bool: self.error(np+'.style.'+flag,'TYPE','必须为布尔值')
                            if 'vertical' in n['style']: self.choice(n['style'],'vertical',{'normal','subscript','superscript'},np+'.style')
                    elif k=='formula':
                        self.choice(n,'status',{'restored','review_required','confirmed','unsupported'},np)
                        self.string(n.get('latex'),np+'.latex',True); self.string(n.get('source_ref'),np+'.source_ref',True)
                        if n.get('latex') is None and not n.get('source_ref'): self.error(np,'UNSUPPORTED_SOURCE','未恢复公式必须保留源引用')
                        if n.get('latex') is None and n.get('status') in {'restored','confirmed'}: self.error(np,'FORMULA_STATUS','已恢复/确认公式必须有 LaTeX')
                    elif k=='image': self.image(n,np)
                    else:
                        self.positive(n.get('width_mm'),np+'.width_mm')
                        if isinstance(n.get('id'),str): targets.add(n['id'])

    def table(self, b, p, targets):
        cols=b.get('columns_mm'); rows=b.get('rows')
        if not self.array(cols,p+'.columns_mm') or not self.array(rows,p+'.rows'): return
        if not cols or not rows: self.error(p,'TABLE_EMPTY','表格必须有行列'); return
        for i,w in enumerate(cols): self.positive(w,f'{p}.columns_mm[{i}]')
        occupied=set()
        for r,row in enumerate(rows):
            rp=f'{p}.rows[{r}]'
            if not self.obj(row,rp,{'id','cells'}): continue
            self.identity(row,rp)
            if not self.array(row.get('cells'),rp+'.cells'): continue
            for c,cell in enumerate(row['cells']):
                cp=f'{rp}.cells[{c}]'
                if not self.obj(cell,cp,{'id','col_index','row_span','col_span','blocks'}): continue
                self.identity(cell,cp); self.blocks(cell.get('blocks'),cp+'.blocks',targets)
                start,rs,cs=(cell.get(k) for k in ('col_index','row_span','col_span'))
                if type(start) is not int or start<0 or type(rs) is not int or rs<1 or type(cs) is not int or cs<1 or start+cs>len(cols) or r+rs>len(rows):
                    self.error(cp,'TABLE_SPAN','表格跨度无效或越界'); continue
                for rr in range(r,r+rs):
                    for cc in range(start,start+cs):
                        if (rr,cc) in occupied: self.error(cp,'TABLE_OVERLAP','表格合并位置重叠')
                        occupied.add((rr,cc))
        if len(occupied)!=len(cols)*len(rows): self.error(p,'TABLE_GAP','表格覆盖存在空洞')

    def issues(self, issues, p):
        if not self.array(issues,p): return
        for i,item in enumerate(issues):
            ip=f'{p}[{i}]'
            if not self.obj(item,ip,{'id','code','severity','question_id','node_id','message'}): continue
            for k in ('id','code','message'): self.string(item.get(k),ip+'.'+k)
            self.choice(item,'severity',{'info','review','error'},ip)
            iid=item.get('id')
            if isinstance(iid,str):
                if iid in self.issue_ids and self.issue_ids[iid]!=item: self.error(ip,'ISSUE_COLLISION','同一 issue.id 的内容不一致')
                self.issue_ids[iid]=item
            for k in ('question_id','node_id'):
                self.string(item.get(k),ip+'.'+k,True)
                if item.get(k) is not None: self.issue_refs.append((ip+'.'+k,k,item[k]))

    def check_issues(self):
        for path,k,value in self.issue_refs:
            if value not in (self.question_ids if k=='question_id' else self.ids): self.error(path,'ISSUE_REFERENCE','问题记录引用不存在')
