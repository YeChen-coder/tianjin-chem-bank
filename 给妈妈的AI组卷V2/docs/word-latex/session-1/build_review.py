"""Build a self-contained full-paper import review, with no editor/storage."""
import base64
import html
import json
import mimetypes
from pathlib import Path

root=Path(__file__).resolve().parents[3]
workspace=root/'.work/session-1'
def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))

def render_paper(directory):
    result=read(directory/'import-result.json');bundle=result['bundle']
    sources=read(directory/'source-records.json');compilation=read(directory/'compilations.json')
    manifest=read(directory/'asset-manifest.json')
    def image(node):
        item=manifest[node['asset_id']];path=Path(item['preview_path'] or item['local_path'])
        mime=mimetypes.guess_type(path.name)[0] or ''
        if path.suffix.lower() not in {'.png','.jpg','.jpeg','.gif','.bmp','.webp','.svg'}:
            return '<span class="review">原矢量图已保存，需查看源 Word</span>'
        uri='data:'+mime+';base64,'+base64.b64encode(path.read_bytes()).decode()
        return f'<img alt="原卷配图" src="{uri}" style="width:{node["width_mm"]}mm;max-width:100%;height:auto">'
    def inline(node):
        if node['type']=='image':return image(node)
        if node['type']=='blank':return '<span class="blank">　　</span>'
        if node['type']=='formula':
            compiled=compilation.get(node['id'],{})
            mml=compiled.get('mathml') if compiled.get('ok') else sources.get(node['source_ref'],{}).get('mathml')
            if mml:return '<span class="formula" title="'+html.escape(node['status'])+'">'+mml+'</span>'
            return '<span class="review">待核对公式：'+html.escape(node['latex'] or '源结构保留')+'</span>'
        text=html.escape(node['text']);style=node.get('style',{})
        if style.get('vertical')=='subscript':text='<sub>'+text+'</sub>'
        elif style.get('vertical')=='superscript':text='<sup>'+text+'</sup>'
        if style.get('bold'):text='<b>'+text+'</b>'
        if style.get('italic'):text='<i>'+text+'</i>'
        return text
    def blocks(items):
        parts=[]
        for block in items:
            if block['type']=='paragraph':
                parts.append('<p style="text-align:'+block['align']+'">'+''.join(inline(n) for n in block['inlines'])+'</p>')
            elif block['type']=='image':parts.append(image(block))
            elif block['type']=='unresolved':parts.append('<aside class="review">'+html.escape(block['reason'])+blocks(block['fallback_blocks'])+'</aside>')
            elif block['type']=='table':
                rows=[]
                for row in block['rows']:
                    rows.append('<tr>'+''.join(f'<td rowspan="{c["row_span"]}" colspan="{c["col_span"]}">'+blocks(c['blocks'])+'</td>' for c in row['cells'])+'</tr>')
                parts.append('<div class="table-wrap"><table>'+''.join(rows)+'</table></div>')
        return ''.join(parts)
    def groups(items):
        return ''.join('<div class="group"><div class="label">'+html.escape(g['label'])+'</div><div>'+blocks(g['blocks'])+groups(g.get('subquestions',[]))+'</div></div>' for g in items)
    cards=[]
    labels={}
    def index_groups(items):
        for g in items:
            labels[g['id']]=g['label'];index_groups(g.get('subquestions',[]))
    for q in bundle['questions']:index_groups(q['subquestions'])
    for q in bundle['questions']:
        answers=[]
        for role,title in [('answers','答案'),('explanations','解析')]:
            for entry in q[role]:
                target=labels.get(entry['target_id'],'整题')
                answers.append('<div class="answer"><b>'+title+' · '+html.escape(target)+'</b>'+blocks(entry['blocks'])+'</div>')
        cards.append('<article><h3>第'+q['number']+'题 <small>'+html.escape(q['qtype'])+'</small></h3>'+blocks(q['body'])+groups(q['options'])+groups(q['subquestions'])+''.join(answers)+'</article>')
    heading=html.escape(bundle['paper']['title'])
    details='<details><summary>待核对清单（'+str(len(bundle['issues']))+'项）</summary><ol>'+''.join('<li>'+html.escape(i['message'])+'</li>' for i in bundle['issues'])+'</ol></details>'
    return '<section class="sample"><h2>'+heading+'</h2><p>'+str(len(bundle['questions']))+'题 · '+str(result['report']['formula_count'])+'个公式节点 · '+str(result['report']['image_count'])+'个配图节点</p>'+details+'<div class="parsed">'+''.join(cards)+'</div><details><summary>展开按原 Word 块顺序的整卷阅读稿</summary>'+blocks(read(directory/'ordered-document.json'))+'</details></section>'

primary=['e4fe1b55db17b1d7ba694e2fd0cbbac18d8c2acc08f93521530df1c765c3cc8c',
         '560f47df6e1832032bdfcce2bd10de460be8d2b5c220ef2fb58f9e1941b764c1',
         '4b12eee89574747ded2aca5114fcaa5a1f4d3faae97ad03d70a9596adf400ac4']
papers=[render_paper(workspace/'real-paper'/sha) for sha in primary]
style='''body{font:17px/1.75 "Segoe UI","Microsoft YaHei",sans-serif;color:#253047;background:#eef1f5;margin:0}header,main{max-width:1060px;margin:auto;padding:24px}header h1{margin:0;font-size:28px}header p{color:#586477}.sample{background:white;padding:28px;margin:20px 0;border-radius:12px}article{padding:20px 0;border-top:1px solid #dbe0e7}h3{font-size:19px}small{color:#64748b;font-weight:400}p{white-space:pre-wrap;margin:8px 0}.group{display:grid;grid-template-columns:35px 1fr;gap:10px;margin:8px 0}.group .group{padding-left:12px}.answer{background:#f5f7fb;border-left:3px solid #94a3b8;margin:12px 0;padding:10px 14px}math{font-size:1.1em}.formula{display:inline-block;max-width:100%;overflow-x:auto;vertical-align:middle}.table-wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;margin:16px 0}td{border:1px solid #8f9aaa;padding:8px;vertical-align:top}img{vertical-align:middle}summary{cursor:pointer;color:#315784}details{margin:18px 0}.review{color:#92400e;background:#fff7ed;padding:4px 8px}li{overflow-wrap:anywhere}.blank{border-bottom:1px solid #111}@media(max-width:600px){header,main{padding:12px}.sample{padding:16px}.group{grid-template-columns:25px 1fr;gap:6px}}'''
output=workspace/'review.html'
output.write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>第一部分 · 真实整卷导入核对</title><style>'+style+'</style><header><h1>真实整卷导入核对</h1><p>三套完整试卷。原件未修改，未写入正式题库。此页展示结构恢复结果；待核对内容仍需教师确认，Word 视觉验收由集成部分执行。</p></header><main>'+''.join(papers)+'</main></html>',encoding='utf-8')
print(output)
