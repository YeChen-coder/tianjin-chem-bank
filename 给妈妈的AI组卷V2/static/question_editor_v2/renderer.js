export function el(tag, cls='', text='') {const n=document.createElement(tag);n.className=cls;n.textContent=text;return n;}
export function button(text, run, cls='') {const n=el('button',cls,text);n.type='button';n.addEventListener('click',run);return n;}
export function safePreviewUrl(url) {
  if(typeof url!=='string'||!url.trim())return null;
  try {const u=new URL(url,location.href);return ['http:','https:'].includes(u.protocol) && u.origin===location.origin ? u.href : null;}catch{return null;}
}
export function renderContent(container, question, assets, {onNode, onCell, onBlocks, editable=false}={}) {
  const assetMap=new Map(assets.map(a=>[a.id,a]));
  const select=(element,node)=>{
    element.dataset.nodeId=node.id;
    if(editable && onNode){element.classList.add('qe-selectable');element.tabIndex=0;element.setAttribute('role','button');element.title='点击编辑';element.addEventListener('click',e=>{e.stopPropagation();onNode(node.id);});element.addEventListener('keydown',e=>{if(e.key==='Enter' || e.key===' '){e.preventDefault();e.stopPropagation();onNode(node.id);}});}
    return element;
  };
  function image(node,inline) {
    const wrap=el(inline?'span':'div',inline?'qe-inline-image':'qe-image');
    const url=safePreviewUrl(assetMap.get(node.asset_id)?.preview_url);
    if(url){const img=el('img');img.src=url;img.alt='试题配图';img.style.width=`${node.width_mm}mm`;img.style.aspectRatio=`${node.width_mm} / ${node.height_mm}`;img.addEventListener('error',()=>{wrap.replaceChildren(el('span','qe-warning','图片暂时无法读取，引用仍保留'));});wrap.append(img);}
    else wrap.append(el('span','qe-warning','图片缺失，原引用仍保留'));
    if(!inline)wrap.style.textAlign=node.align || 'left';return select(wrap,node);
  }
  function inline(node) {
    if(node.type==='text') {const s=el('span','qe-text',node.text);if(node.style?.bold)s.style.fontWeight='bold';if(node.style?.italic)s.style.fontStyle='italic';if(node.style?.vertical && node.style.vertical!=='normal'){s.style.verticalAlign=node.style.vertical==='subscript'?'sub':'super';s.style.fontSize='.78em';}return select(s,node);}
    if(node.type==='formula') {
      const f=el('span','qe-formula');f.setAttribute('aria-label','编辑公式');
      try {if(!node.latex)throw Error('未恢复');if(!globalThis.katex)throw Error('显示资源尚未加载');globalThis.katex.render(node.latex,f,{throwOnError:true,trust:false,strict:'ignore',output:'htmlAndMathml'});}
      catch(e) {f.append(el('span','qe-warning',node.latex || '公式尚未恢复，请核对原卷'));f.dataset.renderError=e.message;}
      if(['review_required','unsupported'].includes(node.status)){f.classList.add('qe-needs-review');f.title='此公式需要核对';}
      return select(f,node);
    }
    if(node.type==='image')return image(node,true);
    if(node.type==='blank'){const b=el('span','qe-blank');b.style.width=`${node.width_mm}mm`;b.setAttribute('aria-label','作答位置');return select(b,node);}
    return el('span','qe-warning',`未支持内容（${node.type}），数据保留`);
  }
  function blocks(parent,list,owner) {
    for(const node of list || []) {
      if(node.type==='paragraph') {const p=el('div','qe-paragraph');p.dataset.nodeId=node.id;p.style.textAlign=node.align || 'left';for(const n of node.inlines)p.append(inline(n));if(!node.inlines.length)p.append(el('span','qe-empty','空段落'));parent.append(p);if(editable && onNode){p.classList.add('qe-editable-paragraph');p.addEventListener('click',e=>{if(e.target===p||e.target.classList.contains('qe-empty')){e.stopPropagation();onNode(node.id);}});const settings=button('⋯',()=>onNode(node.id),'qe-paragraph-settings');settings.setAttribute('aria-label','段落设置');p.append(settings);}}
      else if(node.type==='image')parent.append(image(node,false));
      else if(node.type==='table') {
        const scroll=el('div','qe-table-scroll'),table=el('table','qe-table');table.dataset.nodeId=node.id;table.style.width=`${node.columns_mm.reduce((a,b)=>a+b,0)}mm`;
        const colgroup=el('colgroup');for(const width of node.columns_mm){const col=el('col');col.style.width=`${width}mm`;colgroup.append(col);}table.append(colgroup);
        for(const row of node.rows){const tr=el('tr');for(const c of [...row.cells].sort((a,b)=>a.col_index-b.col_index)){const td=el('td');td.rowSpan=c.row_span;td.colSpan=c.col_span;td.dataset.cellId=c.id;blocks(td,c.blocks,c);if(editable && onCell)td.append(button('单元格设置',()=>onCell(node.id,c.id),'qe-cell-settings'));tr.append(td);}table.append(tr);}
        scroll.append(table);parent.append(scroll);parent.append(el('p','qe-scroll-hint','表格较宽时，可在表格内左右滑动。'));if(editable && onNode)parent.append(button('表格设置',()=>onNode(node.id),'qe-small'));
      } else if(node.type==='unresolved'){const box=el('div','qe-unresolved');box.append(el('p','qe-warning',`需核对：${node.reason}`));blocks(box,node.fallback_blocks,node);parent.append(box);}
      else parent.append(el('p','qe-warning',`未支持的内容块（${node.type}），原数据保留`));
    }
    if(editable && onBlocks)parent.append(button('＋ 添加内容',()=>onBlocks(owner.id),'qe-add'));
  }
  container.replaceChildren();blocks(container,question.body,{id:question.id});
  if(question.options?.length){const list=el('div','qe-options');for(const option of question.options){const row=el('div','qe-option');row.append(select(el('span','qe-label',`${option.label}.`),option));const contents=el('div');blocks(contents,option.blocks,option);row.append(contents);list.append(row);}container.append(list);}
  function subs(parent,list) {for(const sub of list || []){const box=el('div','qe-subquestion');box.append(select(el('div','qe-sub-label',sub.label),sub));blocks(box,sub.blocks,sub);subs(box,sub.subquestions);parent.append(box);}}
  subs(container,question.subquestions);
  const targets=new Map([[question.id,'整道题']]);const scan=list=>(list || []).forEach(s=>{targets.set(s.id,`小问 ${s.label}`);scan(s.subquestions);});scan(question.subquestions);
  for(const [key,label] of [['answers','参考答案'],['explanations','解析']]) if(question[key]?.length){const section=el('section',`qe-${key}`);section.append(el('h4','',label));for(const part of question[key]){const box=el('div','qe-answer-part');box.append(el('span','qe-target',targets.get(part.target_id) || `作答位置 ${part.target_id}`));blocks(box,part.blocks,part);if(editable && onNode)box.append(button('对应位置',()=>onNode(part.id),'qe-small'));section.append(box);}container.append(section);}
  const issues=(question.issues || []).filter(i=>i.severity!=='info');if(issues.length){const list=el('details','qe-issues');list.append(el('summary','',`${issues.length} 项需要核对`));for(const i of issues)list.append(el('p','',i.message));container.append(list);}
}
