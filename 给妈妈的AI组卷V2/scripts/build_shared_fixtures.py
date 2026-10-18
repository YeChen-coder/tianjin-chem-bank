"""Build reproducible, explicitly synthetic v1 controls; not a paper importer."""
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw
from question_content import compile_formula,validate_bundle

ROOT=Path(__file__).resolve().parents[1]


def build():
    dest=ROOT/'fixtures/word_latex_v1/synthetic';dest.mkdir(parents=True,exist_ok=True)
    image=Image.new('RGB',(300,150),'white');d=ImageDraw.Draw(image)
    d.line((40,25,40,120,220,120,220,25),fill='black',width=4)
    d.line((40,80,220,80),fill='blue',width=4);d.ellipse((100,40,120,60),outline='black',width=3)
    image.save(dest/'apparatus.png');sha=hashlib.sha256((dest/'apparatus.png').read_bytes()).hexdigest()
    asset={'id':'asset-'+sha,'sha256':sha,'mime_type':'image/png','preview_url':'/api/v2/content/assets/asset-'+sha}
    seq=0
    def ident(prefix):
        nonlocal seq
        seq+=1;return prefix+'-'+str(seq)
    def text(s,style=None):
        n={'id':ident('text'),'type':'text','text':s}
        if style:n['style']=style
        return n
    def formula(latex,status='restored',source_ref=None):return {'id':ident('formula'),'type':'formula','latex':latex,'status':status,'source_ref':source_ref}
    def para(*inlines):return {'id':ident('paragraph'),'type':'paragraph','align':'left','inlines':list(inlines)}
    def picture(inline=False):
        n={'id':ident('image'),'type':'image','asset_id':asset['id'],'width_mm':40,'height_mm':20}
        if not inline:n['align']='center'
        return n
    def question(number,body):return {'id':'q-control-'+str(number),'revision':0,'number':str(number),'qtype':'化学控制样例','body':body,'options':[],'subquestions':[],'answers':[],'explanations':[],'source':{'source_sha256':None,'number':str(number),'locator':'synthetic-control'},'origin':'import','issues':[]}
    q1=question(1,[para(text('向50 mL溶液中加入'),formula(r'\mathrm{NaOH}'),text('。水的化学式为 H'),text('2',{'vertical':'subscript'}),text('O，判断 '),formula(r'\mathrm{pH}>7'),text('。')),
                    para(text('括号组：'),formula(r'\mathrm{Ca}_{3}{(\mathrm{PO}_{4})}_{2}'),text('；离子：'),formula(r'\mathrm{SO}_{4}^{2-}')),
                    para(formula(r'\mathrm{CaCO}_{3}\overset{900^{\circ}\mathrm{C}}{=}\mathrm{CaO}+\mathrm{CO}_{2}\uparrow'))])
    for label in 'ABCD':q1['options'].append({'id':ident('option'),'label':label,'blocks':[para(text('选项 '+label),*( [picture(True)] if label=='C' else []))]})
    blank={'id':ident('blank'),'type':'blank','width_mm':25}
    sub={'id':ident('subquestion'),'label':'（1）','blocks':[para(text('填写现象：'),blank)],'subquestions':[]}
    q2=question(2,[para(text('观察下图并回答两个小问。')),picture()]);q2['subquestions']=[sub,{'id':ident('subquestion'),'label':'（2）','blocks':[para(text('写出水的化学式：'),formula(r'\mathrm{H}_{2}\mathrm{O}')),picture()],'subquestions':[]}]
    q2['answers']=[{'id':ident('answer'),'target_id':blank['id'],'blocks':[para(text('有气泡产生。'))]},{'id':ident('answer'),'target_id':q2['subquestions'][1]['id'],'blocks':[para(formula(r'\mathrm{H}_{2}\mathrm{O}'))]}]
    q2['explanations']=[{'id':ident('explanation'),'target_id':sub['id'],'blocks':[para(text('答案与作答空及小问分别关联。'))]}]
    def cell(col,blocks,rs=1,cs=1):return {'id':ident('cell'),'col_index':col,'row_span':rs,'col_span':cs,'blocks':blocks}
    def row(cells):return {'id':ident('row'),'cells':cells}
    merged={'id':ident('table'),'type':'table','columns_mm':[40,60,60],'rows':[
        row([cell(0,[para(text('合并表头'))],1,3)]),
        row([cell(0,[para(text('跨两行'))],2),cell(1,[para(formula(r'\mathrm{H}_{2}\mathrm{O}'))]),cell(2,[picture()])]),
        row([cell(1,[para(text('文字与上下标'))]),cell(2,[para(formula(r'x^{2}'))])])]}
    q3=question(3,[para(text('比较表格中的内容。')),merged])
    long={'id':ident('table'),'type':'table','columns_mm':[30,140],'rows':[row([cell(0,[para(text('序号'))]),cell(1,[para(text('跨页表格内容'))])])]}
    for i in range(32):long['rows'].append(row([cell(0,[para(text(str(i+1)))]),cell(1,[para(text('观察并记录实验现象，保留每行内容与表格结构。'))])]))
    q4=question(4,[para(text('长表格分页控制。')),long])
    bundle={'schema_version':1,'paper':{'id':'paper-controls','title':'化学试卷排版控制样例','question_ids':[q['id'] for q in [q1,q2,q3,q4]],'source_sha256':None},'questions':[q1,q2,q3,q4],'assets':[asset],'issues':[]}
    assert validate_bundle(bundle)['ok'],validate_bundle(bundle)
    (dest/'bundle.json').write_text(json.dumps(bundle,ensure_ascii=False,indent=2),encoding='utf-8')
    formulas={n['latex'] for q in bundle['questions'] for n in __import__('question_content.storage',fromlist=['nodes']).nodes(q) if n.get('type')=='formula'}
    results={latex:compile_formula(latex) for latex in sorted(formulas)}
    assert all(r['ok'] for r in results.values()),results
    (dest/'compiled.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    failures={'unknown_command':r'\unsupported{NaOH}','null_source_formula':{'id':'image-formula','type':'formula','latex':None,'status':'review_required','source_ref':'original-image-formula'},'revision_conflict':{'expected_revision':0,'server_revision':1},'unresolved':{'id':'unresolved-font','type':'unresolved','source_ref':'unknown-font-source','reason':'未知字体符号，保留原内容','fallback_blocks':[para(text('原符号候选：?'))]}}
    (dest/'failure_cases.json').write_text(json.dumps(failures,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'fixture':'synthetic','questions':4,'compiled_formulas':len(results),'validation':validate_bundle(bundle)},ensure_ascii=False))


if __name__=='__main__':build()
