"""Private UI fixtures; never imports source Word or edits the old experiments.

The real adapter is evidence reuse for session 2, not session 1's importer.
"""
from pathlib import Path
import argparse, copy, hashlib, json, mimetypes, re, shutil

ROOT = Path(__file__).resolve().parents[2]
OLD = ROOT.parent
WORK = ROOT / '.work/session-2'

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

def control_bundle():
    counter = 0
    def uid():
        nonlocal counter
        counter += 1
        return f'control-node-{counter}'
    def text(s, **style):
        n = {'id':uid(), 'type':'text', 'text':s}
        if style: n['style']=style
        return n
    def formula(latex, status='restored'):
        return {'id':uid(), 'type':'formula', 'latex':latex, 'status':status, 'source_ref':'control-unrestored' if latex is None else None}
    def p(*nodes):
        return {'id':uid(), 'type':'paragraph', 'align':'left', 'inlines':list(nodes)}
    def img(block=False):
        n={'id':uid(),'type':'image','asset_id':'control-image','width_mm':55,'height_mm':35}
        if block: n['align']='center'
        return n
    def q(number, body, **fields):
        n={'id':f'control-q-{number}','revision':0,'number':str(number),'qtype':'结构控制样例','body':body,'options':[],'subquestions':[],'answers':[],'explanations':[], 'source':{'source_sha256':None,'number':str(number),'locator':'synthetic-control'},'origin':'teacher','issues':[]}
        n.update(fields);return n
    blank={'id':uid(),'type':'blank','width_mm':25}
    first=q(1,[p(text('向50 mL溶液中加入'),formula(r'\mathrm{NaOH}'),text('，观察现象。水的化学式为 '),formula(r'\mathrm{H}_{2}\mathrm{O}'),text('。'),blank)],options=[{'id':uid(),'label':chr(65+i),'blocks':[p(text(['酸性','中性','碱性','根据配图判断'][i]),*( [img()] if i==3 else []))]} for i in range(4)])
    first['answers']=[{'id':uid(),'target_id':blank['id'],'blocks':[p(formula(r'\mathrm{H}_{2}\mathrm{O}'))]}]
    first['explanations']=[{'id':uid(),'target_id':first['id'],'blocks':[p(text('这是演示数据，包含 '),formula(r'\mathrm{pH}>7'),text(' 的排版控制，不作为科学答案验收。'))]}]
    reaction=q(2,[p(text('完整反应式：'),formula(r'\mathrm{CaCO}_{3}\overset{260{}^{\circ}\mathrm{C}}{=}\mathrm{CaO}+\mathrm{CO}_{2}')),p(text('括号组和离子电荷：'),formula(r'\mathrm{Ca}(\mathrm{OH})_{2}'),text('；'),formula(r'\mathrm{SO}_{4}^{2-}'))])
    sub1={'id':uid(),'label':'(1)','blocks':[p(text('小问一：阅读配图并填写。')),img(True)],'subquestions':[]}
    sub2={'id':uid(),'label':'(2)','blocks':[p(text('小问二：补充反应条件。'),formula(r'\mathrm{H}_{2}+\mathrm{O}_{2}\xrightarrow{\text{加热}}\mathrm{H}_{2}\mathrm{O}'))],'subquestions':[]}
    third=q(3,[img(True),p(text('图片可以放在小问前后，也可以进入选项或单元格。'))],subquestions=[sub1,sub2],answers=[{'id':uid(),'target_id':sub1['id'],'blocks':[p(text('小问一的答案。'))]},{'id':uid(),'target_id':sub2['id'],'blocks':[p(text('小问二的答案含公式：'),formula(r'\mathrm{H}_{2}\mathrm{O}'))]}])
    table={'id':uid(),'type':'table','columns_mm':[35,45,45],'rows':[
        {'id':uid(),'cells':[{'id':uid(),'col_index':0,'row_span':2,'col_span':1,'blocks':[p(text('合并两行'))]}, {'id':uid(),'col_index':1,'row_span':1,'col_span':2,'blocks':[p(text('合并两列，含公式 '),formula(r'\frac{m}{V}'))]}]},
        {'id':uid(),'cells':[{'id':uid(),'col_index':1,'row_span':1,'col_span':1,'blocks':[p(text('溶液')),p(formula(r'\mathrm{NaOH}'))]}, {'id':uid(),'col_index':2,'row_span':1,'col_span':1,'blocks':[p(img())]}]}
    ]}
    fourth=q(4,[p(text('表格里的文字、公式和图片也可以编辑。')),table])
    fifth=q(5,[p(text('待恢复的公式：'),formula(None,'unsupported')),{'id':uid(),'type':'unresolved','source_ref':'control-unknown','reason':'演示未知符号，保留可见原内容','fallback_blocks':[p(text('原内容：□'))]}],issues=[{'id':uid(),'code':'UNMAPPED_SYMBOL','severity':'review','question_id':'control-q-5','node_id':None,'message':'未知符号需对照原件，不能自动猜测。'}])
    questions=[first,reaction,third,fourth,fifth]
    # A tiny original vector test asset is generated here, not by image editing.
    svg='<svg xmlns="http://www.w3.org/2000/svg" width="220" height="140" viewBox="0 0 220 140"><rect width="220" height="140" fill="white"/><path d="M70 25v65q0 25 40 25t40-25V25M70 78h80M55 25h110" fill="none" stroke="#28644d" stroke-width="4"/><path d="M74 82h72v8q0 21-36 21T74 90" fill="#d3ebe3"/><text x="110" y="133" text-anchor="middle" font-family="sans-serif" font-size="12">测试配图</text></svg>'
    (WORK/'assets').mkdir(parents=True, exist_ok=True)
    asset_path=WORK/'assets/control.svg';asset_path.write_text(svg,encoding='utf-8')
    return {'schema_version':1,'paper':{'id':'control-paper','title':'化学内容编辑 · 结构控制样例','question_ids':[q['id'] for q in questions],'source_sha256':None},'questions':questions,'assets':[{'id':'control-image','sha256':digest(asset_path),'mime_type':'image/svg+xml','preview_url':'/api/v2/content/assets/control-image'}],'issues':[]}

def real_bundle():
    source=OLD/'experiments/formula_normalization_lab/.sandbox/whole-paper-latex/model.json'
    if not source.is_file():return None, {'available':False,'reason':'旧整卷实验模型不存在，请改用会话 1 的 bundle。'}
    original=json.loads(source.read_text(encoding='utf-8'));ledger=[];counter=0;escape_fixes=0
    def uid():
        nonlocal counter
        counter+=1;return f'real-node-{counter}'
    def inline(n):
        nonlocal escape_fixes
        common={'id':uid()}
        if n['kind']=='break':return {**common,'type':'text','text':'\n'}
        if n['kind']=='text':
            style={k:n[k] for k in ['bold','italic'] if k in n}
            if n.get('vert'):style['vertical']=n['vert']
            return {**common,'type':'text','text':n['text'],**({'style':style} if style else {})}
        if n['kind']=='math':
            latex=n.get('latex')
            # The historical serialized model doubled command backslashes.
            # Only doubled slashes immediately before alphabetic commands are decoded.
            if latex:
                decoded=re.sub(r'\\\\(?=[a-zA-Z])',lambda _:'\\',latex)
                if decoded!=latex:escape_fixes+=1
                latex=decoded
            return {**common,'type':'formula','latex':latex,'status':'restored' if latex else 'unsupported','source_ref':f"experiment:{n.get('id',common['id'])}"}
        if n['kind']=='image':return {**common,'type':'image','asset_id':n['asset'],'width_mm':n.get('width_pt',100)*25.4/72,'height_mm':n.get('height_pt',100)*25.4/72}
        raise ValueError(f"Unknown historical inline: {n['kind']}")
    def block(b):
        if b['kind']=='paragraph':return {'id':uid(),'type':'paragraph','align':b.get('align') if b.get('align') in ('left','center','right') else 'left','inlines':[inline(n) for n in b['inlines']]}
        if b['kind']=='table':
            rows=[];columns=max(sum(c.get('colspan',1) for c in row) for row in b['rows'])
            for row in b['rows']:
                cells=[];col=0
                for c in row:
                    if c.get('vmerge') is not None:raise ValueError('Historical vertically merged table requires explicit adapter; content must not be flattened.')
                    cs=c.get('colspan',1);cells.append({'id':uid(),'col_index':col,'row_span':1,'col_span':cs,'blocks':[block(n) for n in c['blocks']]});col+=cs
                rows.append({'id':uid(),'cells':cells})
            return {'id':uid(),'type':'table','columns_mm':[40]*columns,'rows':rows}
        raise ValueError(f"Unknown historical block: {b['kind']}")
    def question(number):return {'id':f'real-q-{number}','revision':0,'number':str(number) if number else '', 'qtype':'整卷实验样本' if number else '卷首说明','body':[],'options':[],'subquestions':[],'answers':[],'explanations':[],'source':{'source_sha256':original['source_sha256'],'number':str(number),'locator':'historical-whole-paper-display-only'},'origin':'import','issues':[]}
    current=question(0);questions=[current];owner=current['body'];numbers=[]
    for i,b in enumerate(original['questions'][0]['blocks']):
        s=''.join(n.get('text','') for n in b.get('inlines',[]))
        match=re.match(r'^(\d{1,2})[．.]',s)
        if match:
            number=int(match[1]);numbers.append(number);current=question(number);questions.append(current);owner=current['body']
        converted=block(b)
        if b.get('role')=='option':
            option={'id':uid(),'label':b.get('label') or chr(65+len(current['options'])),'blocks':[converted]};current['options'].append(option)
        elif b.get('role')=='subquestion':
            sub={'id':uid(),'label':re.match(r'^\(?\d+\)',s).group(0) if re.match(r'^\(?\d+\)',s) else '小问','blocks':[converted],'subquestions':[]};current['subquestions'].append(sub);owner=sub['blocks']
        else: owner.append(converted)
        ledger.append({'original_block_index':i,'question_id':current['id'],'node_id':converted['id']})
    if numbers!=list(range(1,27)):raise ValueError(f'Unexpected question markers: {numbers}')
    assets=[];asset_dir=source.parent/'assets'
    used=set()
    def scan(value):
        if isinstance(value,dict):
            if value.get('type')=='image':used.add(value['asset_id'])
            for v in value.values():scan(v)
        elif isinstance(value,list):
            for v in value:scan(v)
    scan(questions)
    media_manifest=[]
    for id in sorted(used):
        asset=original['assets'][id];media=asset_dir/asset['original'];preview=asset_dir/asset.get('preview',asset['original'])
        for path in set([media,preview]):
            destination=WORK/'assets'/path.name
            if not destination.exists():shutil.copyfile(path,destination)
            if digest(destination)!=digest(path):raise ValueError('Existing private asset differs from source; refusing to overwrite.')
            media_manifest.append({'source':str(path),'destination':str(destination),'sha256':digest(path),'purpose':'real UI fixture asset'})
        assets.append({'id':id,'sha256':digest(media),'mime_type':mimetypes.guess_type(media.name)[0] or 'application/octet-stream','preview_url':f'/api/v2/content/assets/{id}'})
    bundle={'schema_version':1,'paper':{'id':'real-paper','title':original['title'],'question_ids':[q['id'] for q in questions],'source_sha256':original['source_sha256']},'questions':questions,'assets':assets,'issues':[]}
    report={'available':True,'source_model':str(source),'source_model_sha256':digest(source),'original_word_sha256':original['source_sha256'],'question_count':len(numbers),'intro_card_count':1,'blocks_accounted_for':len(ledger),'historical_command_escape_fixes':escape_fixes,'ledger':ledger,'asset_manifest':media_manifest,'limits':'Display-only historical adapter; section headings retained in existing block order. Answers absent in this source. Split and answer mapping require session 1 validation.'}
    return bundle,report

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--reset-state',action='store_true');parser.add_argument('--import-result',type=Path);args=parser.parse_args()
    WORK.mkdir(parents=True,exist_ok=True)
    fixtures={'control-paper':control_bundle()};real,report=real_bundle()
    if real:fixtures['real-paper']=real
    if args.import_result:
        path=args.import_result.resolve()
        if not path.is_relative_to(ROOT):raise ValueError('Use a V2 import-result file; old source remains read-only.')
        result=json.loads(path.read_text(encoding='utf-8'));bundle=result['bundle'];manifest=json.loads((path.parent/'asset-manifest.json').read_text(encoding='utf-8'));previews={};copies=[]
        for asset in bundle['assets']:
            record=manifest[asset['id']];preview=Path(record.get('preview_path') or record['local_path']).resolve()
            if not preview.is_relative_to(path.parent):raise ValueError('Import preview escapes its workspace.')
            destination=WORK/'assets'/preview.name
            if not destination.exists():shutil.copyfile(preview,destination)
            if digest(destination)!=digest(preview):raise ValueError('Private preview hash mismatch.')
            previews[asset['id']]=destination.name;copies.append({'source':str(preview),'destination':str(destination),'sha256':digest(preview)})
        fixtures['imported-paper']=bundle
        write(WORK/'imported-asset-previews.json',previews);write(WORK/'imported-provenance.json',{'source':str(path),'sha256':digest(path),'report':result['report'],'copied_previews':copies})
    write(WORK/'fixtures.json',fixtures);write(WORK/'fixture-provenance.json',report)
    if args.reset_state or not (WORK/'state.json').exists():write(WORK/'state.json',fixtures)
    print(json.dumps({'papers':list(fixtures),'private_workspace':str(WORK),'real_questions':26 if real else 0},ensure_ascii=False))

if __name__=='__main__':main()
