import {clone,newId,walk,locate,paragraph,targetList,validateQuestion,tableGrid,mergeRight,mergeDown,splitCell,appendRow,appendColumn,moveNode} from './model.js';
import {el,button,renderContent,safePreviewUrl} from './renderer.js';
export {createContentApi,ContentApiError} from './api.js';
const equal=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
function labelled(label,input,parent) {const wrap=el('label','qe-field-label',label);wrap.append(input);parent.append(wrap);return input;}
function input(value,type='text'){const n=el('input');n.type=type;n.value=value;return n;}
function select(items,value){const n=el('select');for(const [id,label] of items){const o=el('option','',label);o.value=id;n.append(o);}n.value=value ?? items[0]?.[0];return n;}
function dialog(host,title,build){const d=el('dialog','qe-dialog');d.append(el('h3','',title));host.append(d);let cleanup=()=>{};d.addEventListener('close',()=>{cleanup();d.remove();},{once:true});d.addEventListener('cancel',()=>{});cleanup=build(d,()=>d.close()) || cleanup;d.showModal();return d;}
function downloadJson(data,name){const u=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=el('a');a.href=u;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(u),1000);}

export function mountQuestionEditor(container,{question,assets=[],api,onSaved=()=>{},draftNamespace='question-editor-v1'}={}) {
  if(!question || !api)throw Error('question 和 api 是必填参数。');
  let saved=clone(question),draft=clone(question),currentAssets=clone(assets),undo=[],redo=[],dead=false,saving=false,activeDialog=null,epoch=0;
  const controllers=new Set(), storageKey=`${draftNamespace}:${question.id}`;
  const root=el('article','qe-card');root.dataset.questionId=question.id;container.append(root);
  const header=el('header','qe-card-header'),title=el('h3'),state=el('span','qe-state'),toolbar=el('div','qe-toolbar'),message=el('p','qe-message');message.setAttribute('role','status');message.setAttribute('aria-live','polite');
  const content=el('div','qe-content'),recovery=el('div','qe-recovery');root.append(header,recovery,toolbar,message,content);header.append(title,state);
  function notice(text,error=false){message.textContent=text;message.classList.toggle('qe-error',error);}
  function store(){try {if(equal(saved,draft))localStorage.removeItem(storageKey);else localStorage.setItem(storageKey,JSON.stringify({schema_version:1,base_revision:saved.revision,question:draft,saved_at:new Date().toISOString()}));}catch{notice('浏览器无法保留临时草稿，请保存或下载草稿后再关闭。',true);}}
  function clearStored(){try{localStorage.removeItem(storageKey);}catch{}}
  function change(run){if(saving || dead)return false;const before=clone(draft);try{run();const errors=validateQuestion(draft,currentAssets);if(errors.length)throw Error(errors.join(' '));undo.push(before);redo=[];notice('修改已应用到草稿。');store();render();return true;}catch(e){draft=before;notice(e.message,true);return false;}}
  function closeDialog(){epoch++;activeDialog?.close();activeDialog=null;}
  const saveBtn=button('保存本题',save,'qe-primary'),cancelBtn=button('取消本题修改',cancel),undoBtn=button('撤销',()=>{if(undo.length&&!saving){redo.push(clone(draft));draft=undo.pop();store();render();}}),redoBtn=button('重做',()=>{if(redo.length&&!saving){undo.push(clone(draft));draft=redo.pop();store();render();}});
  const addSectionBtn=button('添加选项 / 小问 / 答案',()=>addSection());
  toolbar.append(saveBtn,cancelBtn,undoBtn,redoBtn,addSectionBtn,button('下载草稿 JSON',()=>downloadJson({schema_version:1,question:draft,assets:currentAssets},`${draft.id}-draft.json`),'qe-small'));
  function render(){
    title.textContent=`编辑题目 · ${draft.qtype || '题目'}`;
    root.classList.toggle('qe-dirty',!equal(saved,draft));root.setAttribute('aria-busy',String(saving));recovery.inert=saving;state.textContent=saving?'正在保存…':equal(saved,draft)?`已保存 · 版本 ${saved.revision}`:'有未保存修改';
    saveBtn.disabled=saving||equal(saved,draft);cancelBtn.disabled=saving||equal(saved,draft);undoBtn.disabled=saving||!undo.length;redoBtn.disabled=saving||!redo.length;addSectionBtn.disabled=saving;
    renderContent(content,draft,currentAssets,{editable:!saving,onNode:editNode,onCell:editCell,onBlocks:addBlocks});
  }
  async function save(){
    if(saving||dead||equal(saved,draft))return;
    closeDialog();const errors=validateQuestion(draft,currentAssets);if(errors.length){notice(errors.join(' '),true);return;}
    const snapshot=clone(draft),baseRevision=saved.revision,controller=new AbortController();controllers.add(controller);saving=true;notice('正在保存整道题…');render();
    try {
      const result=await api.saveQuestion(saved.id,{schema_version:1,expected_revision:baseRevision,question:snapshot},{signal:controller.signal});
      if(dead)return;
      if(result.schema_version!==1 || result.question?.id!==saved.id || !Number.isInteger(result.question.revision) || result.question.revision<=baseRevision)throw Error('保存响应的身份或版本无效；请读取最新版本核对。');
      saved=clone(result.question);draft=clone(saved);currentAssets=clone(result.assets ?? currentAssets);undo=[];redo=[];clearStored();notice('本题已保存。');
      try{onSaved(clone(saved),clone(currentAssets));}catch{notice('本题已保存，但整卷界面更新失败；请刷新核对。',true);}
    } catch(e) {
      if(dead)return;
      if(e.status===409){notice('这道题已有新版本。你的草稿仍保留，请对照后重新编辑。',true);showConflict(e);}
      else notice(`${e.message || '保存未完成'} 草稿仍保留。若连接中断，请读取最新版本核对是否已保存。`,true);
    } finally {controllers.delete(controller);saving=false;if(!dead)render();}
  }
  function cancel(){if(saving)return;closeDialog();draft=clone(saved);undo=[];redo=[];clearStored();render();notice('已取消本题的未保存修改。');}
  function showConflict(error){
    activeDialog=dialog(root,'版本冲突：保留草稿并核对',(d,close)=>{
      const text=el('p','','不会自动覆盖新版本。先下载草稿，再选择读取最新题目。');d.append(text);
      const comparison=el('div','qe-comparison'),status=el('p','qe-message');d.append(comparison,status);
      let latest=null,closed=false;const controller=new AbortController();controllers.add(controller);
      comparison.append(el('h4','','你的草稿'));const mine=el('div');renderContent(mine,draft,currentAssets);comparison.append(mine);
      const load=button('读取最新版本进行对照',async()=>{load.disabled=true;try{latest=await api.getQuestion(saved.id,{signal:controller.signal});if(closed||dead)return;if(latest.schema_version!==1||latest.question?.id!==saved.id)throw Error('最新版本响应无效。');comparison.append(el('h4','',`服务器版本 ${latest.question.revision}`));const theirs=el('div');renderContent(theirs,latest.question,latest.assets);comparison.append(theirs);replace.disabled=false;}catch(e){if(!closed)status.textContent=e.message;}finally{if(!closed)load.disabled=false;}});
      const replace=button('放弃草稿，使用最新版本',()=>{if(latest){saved=clone(latest.question);draft=clone(saved);currentAssets=clone(latest.assets);undo=[];redo=[];clearStored();close();render();onSaved(clone(saved),clone(currentAssets));notice('已载入最新题目，请在这个版本上重新编辑。');}});replace.disabled=true;
      d.append(load,button('下载我的草稿',()=>downloadJson({schema_version:1,question:draft,assets:currentAssets},`${draft.id}-conflict.json`)),replace,button('保留草稿并关闭',close));
      return ()=>{closed=true;controller.abort();controllers.delete(controller);};
    });
  }
  function open(title,build){if(saving||dead)return;closeDialog();activeDialog=dialog(root,title,build);}
  function editNode(id){const node=locate(draft,id)?.node;if(!node)return;
    if(node.type==='formula')return formulaDialog(id);
    if(node.type==='image')return imageDialog(id);
    if(node.type==='table')return tableDialog(id);
    if(node.type==='text')return textDialog(id);
    if(node.type==='paragraph')return paragraphDialog(id);
    if(node.type==='blank')return open('调整作答位置',(d,close)=>{const width=labelled('宽度（毫米）',input(node.width_mm,'number'),d);d.append(button('应用',()=>{change(()=>{locate(draft,id).node.width_mm=Number(width.value);});if(!message.classList.contains('qe-error'))close();}),button('取消',close));});
    if(node.target_id)return open('答案或解析的对应位置',(d,close)=>{const targets=labelled('对应到',select(targetList(draft).map(t=>[t.id,t.label]),node.target_id),d);d.append(button('应用',()=>{change(()=>{locate(draft,id).node.target_id=targets.value;});close();}),button('取消',close));});
    if(node.label!==undefined && node.blocks)return open('修改选项或小问编号',(d,close)=>{const label=labelled('编号 / 标记',input(node.label),d);d.append(button('应用编号',()=>{if(!label.value.trim()){notice('编号不能为空。',true);return;}change(()=>{locate(draft,id).node.label=label.value;});close();}),button('取消',close));});
  }
  function textDialog(id){const node=locate(draft,id).node;open('编辑文字',(d,close)=>{
    const text=labelled('文字内容',el('textarea'),d);text.value=node.text;text.rows=5;
    const vertical=labelled('文字位置',select([['normal','普通'],['subscript','下标'],['superscript','上标']],node.style?.vertical || 'normal'),d);
    const bold=labelled('加粗',input('','checkbox'),d);bold.checked=!!node.style?.bold;const italic=labelled('斜体',input('','checkbox'),d);italic.checked=!!node.style?.italic;
    d.append(el('p','qe-hint','只修改这段文字，旁边的公式和图片保持原位置。选中文字后可转为公式。'));
    d.append(button('将选中文字变为公式',()=>{
      const start=text.selectionStart,end=text.selectionEnd;if(start===end){notice('请先在文字框中选中要变为公式的部分。',true);return;}
      const pending={id,start,end,text:text.value,style:{...node.style,vertical:vertical.value,bold:bold.checked,italic:italic.checked}};close();formulaDialog(null,pending);
    }),button('应用文字修改',()=>{change(()=>{const n=locate(draft,id).node;n.text=text.value;n.style={...n.style,vertical:vertical.value,bold:bold.checked,italic:italic.checked};});close();notice('文字已应用到草稿，请保存本题。');},'qe-primary'),button('取消',close));
  });}
  function formulaDialog(id,selection=null,insert=null){
    const original=id?clone(locate(draft,id).node):null;
    open('可视化编辑公式',(d,close)=>{
      if(!globalThis.MathfieldElement){d.append(el('p','qe-warning','公式编辑资源尚未加载，请联系维护者接入 MathLive。'),button('关闭',close));return;}
      const token=++epoch,controller=new AbortController();controllers.add(controller);let closed=false,serial=0;
      const field=el('math-field');field.value=original?.latex || '';field.mathVirtualKeyboardPolicy='manual';
      const hint=el('p','qe-hint',selection?`替换选中文字：${selection.text.slice(selection.start,selection.end)}`:'直接点公式里的数字或符号修改；也可用下方模板。长公式可在框内左右滑动。');
      const templates=el('div','qe-templates');
      for(const [label,latex] of [['下标','_{#0}'],['上标 / 电荷','^{#0}'],['分数','\\frac{#0}{#?}'],['反应箭头','\\rightarrow'],['带条件箭头','\\xrightarrow{#0}'],['带条件等号','\\overset{#0}{=}'],['括号组','\\left(#0\\right)_{#?}'],['摄氏度','{}^{\\circ}\\mathrm{C}'],['化学式','\\mathrm{#0}']])templates.append(button(label,()=>{field.focus();field.insert(latex);}));
      templates.append(button('打开公式键盘',()=>{field.focus();globalThis.mathVirtualKeyboard?.show();}));
      const advanced=el('details','qe-advanced');advanced.append(el('summary','','高级：公式源码'));const source=el('textarea');source.value=field.value;source.rows=3;advanced.append(source,button('从源码更新公式',()=>{field.value=source.value;serial++;}));field.addEventListener('input',()=>{source.value=field.value;serial++;});
      const status=el('p','qe-message');status.setAttribute('role','status');const apply=button('检查并应用到草稿',async()=>{
        const latex=field.value,version=serial,requestId=newId();if(!latex.trim()){status.textContent='公式不能为空。';return;}apply.disabled=true;status.textContent='正在检查公式…';
        try{
          const result=await api.compileFormula({latex,source_ref:original?.source_ref ?? null,request_id:requestId},{signal:controller.signal});
          if(closed||dead||epoch!==token)return;
          if(serial!==version||field.value!==latex){status.textContent='公式已继续修改，请重新检查。';return;}
          if(result.request_id!==requestId || result.latex!==latex)throw Error('公式检查响应不匹配，请重试。');
          if(!result.ok)throw Error((result.diagnostics || []).map(x=>typeof x==='string'?x:x.message).join('；') || '公式无法编译。');
          if(!result.mathml || !result.omml)throw Error('公式检查没有返回完整结构，请维护者核对编译接口。');
          // No MathML/OMML is inserted into browser HTML or treated as editing truth.
          change(()=>{
            if(id){const n=locate(draft,id).node;if(n.latex!==latex)n.status='review_required';n.latex=latex;}
            else if(selection){const entry=locate(draft,selection.id);const i=Number(entry.key),before=selection.text.slice(0,selection.start),after=selection.text.slice(selection.end),nodes=[];
              if(before)nodes.push({...entry.node,text:before,style:selection.style});
              nodes.push({id:newId(),type:'formula',latex,status:'review_required',source_ref:null});
              if(after)nodes.push({...entry.node,id:before?newId():entry.node.id,text:after,style:selection.style});entry.parent.splice(i,1,...nodes);
            } else if(insert){const n={id:newId(),type:'formula',latex,status:'review_required',source_ref:null};if(typeof insert==='string')locate(draft,insert).node.inlines.push(n);else{const p=paragraph();p.inlines=[n];(insert.ownerId===draft.id?draft.body:locate(draft,insert.ownerId).node.blocks).push(p);}}
          });close();notice(result.compiler_version?.startsWith('mock')?'公式已应用到测试草稿；这次检查使用模拟编译结果。':'公式已检查并应用到草稿，请保存本题。');
        }catch(e){if(!closed)status.textContent=e.message;}finally{if(!closed)apply.disabled=false;}
      },'qe-primary');d.append(hint,field,templates,advanced,status,apply,button('取消',close));setTimeout(()=>{if(!closed)field.focus();},0);
      return ()=>{closed=true;controller.abort();controllers.delete(controller);globalThis.mathVirtualKeyboard?.hide();};
    });
  }
  function blockOwners(){const owners=[{id:draft.id,label:'题干',blocks:draft.body}];walk(draft,n=>{if(n!==draft&&Array.isArray(n.blocks))owners.push({id:n.id,label:n.target_id?'答案 / 解析':n.col_index!==undefined?`表格第 ${n.col_index+1} 列单元格`:n.label?`选项 / 小问 ${n.label}`:`内容区域 ${n.id.slice(-6)}`,blocks:n.blocks});});return owners;}
  function imageDialog(id){const node=clone(locate(draft,id).node);open('调整图片尺寸与位置',(d,close)=>{
    const preview=el('img','qe-image-dialog-preview'),url=safePreviewUrl(currentAssets.find(a=>a.id===node.asset_id)?.preview_url);if(url){preview.src=url;preview.alt='原配图';d.append(preview);}
    const width=labelled('宽度（毫米，保持原比例）',input(node.width_mm,'number'),d);width.min='1';width.step='0.5';
    const align=labelled('独立图片对齐',select([['left','左对齐'],['center','居中'],['right','右对齐']],node.align || 'left'),d);
    const destinations=[];for(const owner of blockOwners()){destinations.push([`b:${owner.id}`,`${owner.label} · 独立图片`]);for(const p of owner.blocks.filter(b=>b.type==='paragraph'))destinations.push([`i:${p.id}`,`${owner.label} · 段落内（${p.inlines.filter(i=>i.type==='text').map(i=>i.text).join('').slice(0,18)||'空段落'}）`]);}
    const placement=labelled('放置区域',select([['keep','保持原位置'],...destinations],'keep'),d),position=labelled('新区域内的位置',select([['end','末尾'],['0','开头']],'end'),d);
    placement.addEventListener('change',()=>{const [mode,ownerId]=placement.value.split(':');const owner=ownerId===draft.id?{blocks:draft.body}:locate(draft,ownerId)?.node;const list=(mode==='b'?owner?.blocks:owner?.inlines)?.filter(n=>n.id!==id) || [];position.replaceChildren();for(const [value,label] of [['0','开头'],...list.slice(1).map((n,i)=>[String(i+1),`第 ${i+2} ${mode==='b'?'段内容':'个行内内容'}之前`]),['end','末尾']]){const o=el('option','',label);o.value=value;position.append(o);}position.value='end';});
    d.append(button('应用图片修改',()=>{change(()=>{const n=locate(draft,id).node,w=Number(width.value);if(w<=0||!Number.isFinite(w))throw Error('宽度必须大于零。');n.width_mm=w;n.height_mm=w*node.height_mm/node.width_mm;if(placement.value!=='keep'){const [mode,destinationId]=placement.value.split(':');const owner=destinationId===draft.id?{...draft,blocks:draft.body}:locate(draft,destinationId).node;const index=position.value==='end'?(mode==='b'?owner.blocks.length:owner.inlines.length):Number(position.value);if(destinationId===draft.id){const entry=locate(draft,id);entry.parent.splice(Number(entry.key),1);if(mode==='b')n.align=align.value;draft.body.splice(Math.min(index,draft.body.length),0,n);}else moveNode(draft,id,destinationId,index,mode==='b');}
      const n2=locate(draft,id).node;if(n2.align!==undefined)n2.align=align.value;
    });if(!message.classList.contains('qe-error')){close();notice('图片已应用到草稿，请保存本题。');}},'qe-primary'),button('取消',close));
  });}
  function tableDialog(id){open('表格设置',(d,close)=>{
    const local=clone(locate(draft,id).node);const status=el('p','qe-message'),widths=el('div');
    function paint(){widths.replaceChildren();local.columns_mm.forEach((w,i)=>{const field=labelled(`第 ${i+1} 列宽度（毫米）`,input(w,'number'),widths);field.addEventListener('input',()=>{local.columns_mm[i]=Number(field.value);});});}
    d.append(el('p','qe-hint','宽表格在页面内滚动。新增行列保留已有单元格，合并设置请点对应单元格。'),widths);
    d.append(button('增加一行',()=>{try{appendRow(local);paint();}catch(e){status.textContent=e.message;}}),button('增加一列',()=>{try{appendColumn(local);paint();}catch(e){status.textContent=e.message;}}));
    const empty=c=>c.blocks.every(b=>b.type==='paragraph'&&b.inlines.every(i=>i.type==='text'&&!i.text.trim()));
    d.append(button('删除末尾空行',()=>{try{tableGrid(local);const last=local.rows.at(-1),grid=tableGrid(local);if(local.rows.length<2||last.cells.some(c=>c.row_span!==1||!empty(c))||grid.at(-1).some(c=>!last.cells.includes(c)))throw Error('只能删除不含内容、未被合并覆盖的末尾空行。');local.rows.pop();paint();}catch(e){status.textContent=e.message;}}),button('删除末尾空列',()=>{try{const grid=tableGrid(local),last=local.columns_mm.length-1;if(last<1||grid.some(row=>row[last].col_index!==last||row[last].col_span!==1||row[last].row_span!==1||!empty(row[last])))throw Error('只能删除无内容、未合并的末尾空列。');local.rows.forEach(r=>r.cells=r.cells.filter(c=>c.col_index!==last));local.columns_mm.pop();paint();}catch(e){status.textContent=e.message;}}));
    d.append(status,button('应用表格修改',()=>{try{tableGrid(local);change(()=>{Object.assign(locate(draft,id).node,local);});close();notice('表格结构已应用到草稿。');}catch(e){status.textContent=e.message;}},'qe-primary'),button('取消',close));paint();
  });}
  function editCell(tableId,cellId){open('单元格设置',(d,close)=>{const status=el('p','qe-message');d.append(el('p','qe-hint','合并后两格的全部内容按原顺序保留；拆分时原内容留在左上角。'),status);for(const [label,operation] of [['与右侧合并',mergeRight],['与下方合并',mergeDown],['拆分此合并格',splitCell]])d.append(button(label,()=>{change(()=>operation(locate(draft,tableId).node,cellId));if(message.classList.contains('qe-error'))status.textContent=message.textContent;else close();}));d.append(button('关闭',close));});}
  function paragraphDialog(id){const node=locate(draft,id).node;open('段落设置',(d,close)=>{const align=labelled('对齐方式',select([['left','左对齐'],['center','居中'],['right','右对齐']],node.align || 'left'),d);d.append(button('添加文字',()=>{change(()=>{locate(draft,id).node.inlines.push({id:newId(),type:'text',text:'新文字'});});close();}),button('插入公式',()=>{close();formulaDialog(null,null,id);}),button('插入作答空',()=>{change(()=>{locate(draft,id).node.inlines.push({id:newId(),type:'blank',width_mm:25});});close();}),button('插入行内图片',()=>{close();insertAsset(id,true);}),button('应用对齐',()=>{change(()=>{locate(draft,id).node.align=align.value;});close();}),button('取消',close));});}
  function addBlocks(ownerId){open('添加内容',(d,close)=>{const append=()=>ownerId===draft.id?draft.body:locate(draft,ownerId).node.blocks;
    d.append(button('文字段落',()=>{const p=paragraph('新段落');change(()=>append().push(p));close();textDialog(p.inlines[0].id);}),button('公式段落',()=>{close();formulaDialog(null,null,{ownerId});}),button('独立图片',()=>{close();insertAsset(ownerId,false);}),button('表格',()=>{change(()=>append().push({id:newId(),type:'table',columns_mm:[40,40],rows:[0,1].map(()=>({id:newId(),cells:[0,1].map(c=>({id:newId(),col_index:c,row_span:1,col_span:1,blocks:[paragraph()]}))}))}));close();}),button('取消',close));
  });}
  function insertAsset(ownerId,inline){open('选择已有配图',(d,close)=>{if(!currentAssets.length)d.append(el('p','','这道题没有已登记图片。图片上传由主程序提供。'));for(const a of currentAssets){const b=button('使用这张图片',async()=>{const img={id:newId(),type:'image',asset_id:a.id,width_mm:40,height_mm:40};const preview=b.querySelector('img');if(preview){try{await preview.decode();}catch{notice('图片预览无法读取，请先核对资产。',true);return;}if(dead||!d.open)return;img.height_mm=40*preview.naturalHeight/preview.naturalWidth;}change(()=>{if(inline)locate(draft,ownerId).node.inlines.push(img);else{img.align='center';(ownerId===draft.id?draft.body:locate(draft,ownerId).node.blocks).push(img);}});close();});const url=safePreviewUrl(a.preview_url);if(url){const img=el('img','qe-asset-thumb');img.src=url;img.alt='可选配图';b.prepend(img);}d.append(b);}d.append(button('取消',close));});}
  function addSection(){open('添加题目内容区域',(d,close)=>{const kind=labelled('内容类型',select([['options','选项'],['subquestions','小问'],['answers','答案'],['explanations','解析']]),d),label=labelled('编号 / 标记',input(''),d),target=labelled('答案、解析对应位置',select(targetList(draft).map(t=>[t.id,t.label]),draft.id),d);d.append(button('添加',()=>{change(()=>{const section=kind.value,n={id:newId(),blocks:[paragraph('新内容')]};if(section==='options')n.label=label.value||String.fromCharCode(65+draft.options.length);else if(section==='subquestions'){n.label=label.value||`(${draft.subquestions.length+1})`;n.subquestions=[];}else n.target_id=target.value;draft[section].push(n);});close();}),button('取消',close));});}
  try{const previous=JSON.parse(localStorage.getItem(storageKey)||'null');if(previous?.schema_version===1&&previous.question?.id===draft.id&&!equal(previous.question,saved)){
    recovery.append(el('p','',`找到本地未保存草稿（基于版本 ${previous.base_revision}）。`),button('恢复草稿',()=>{if(previous.base_revision!==saved.revision){notice('服务器版本已变化，草稿不能直接覆盖。请下载后对照。',true);downloadJson(previous,`${draft.id}-recovery.json`);return;}const errors=validateQuestion(previous.question,currentAssets);if(errors.length){notice(errors.join(' '),true);return;}undo.push(clone(draft));draft=clone(previous.question);recovery.replaceChildren();render();notice('草稿已恢复，请核对并保存。');}),button('丢弃本地草稿',()=>{clearStored();recovery.replaceChildren();}));
  }}catch{notice('本地草稿无法读取，已保存内容仍可编辑。',true);}
  const unload=e=>{if(!equal(saved,draft)||saving){e.preventDefault();e.returnValue='';}};window.addEventListener('beforeunload',unload);render();
  return {
    getDraft:()=>clone(draft),getSaved:()=>clone(saved),isDirty:()=>!equal(saved,draft),isSaving:()=>saving,save,cancel,
    readLatest:async()=>{if(dead||saving)return;try{const latest=await api.getQuestion(saved.id);if(dead)return;if(!equal(saved,draft)){showConflict({});return;}if(latest.schema_version!==1||latest.question?.id!==saved.id)throw Error('读取响应无效。');saved=clone(latest.question);draft=clone(saved);currentAssets=clone(latest.assets);render();onSaved(clone(saved),clone(currentAssets));}catch(e){if(!dead)notice(e.message,true);}},
    destroy:()=>{dead=true;closeDialog();controllers.forEach(c=>c.abort());controllers.clear();window.removeEventListener('beforeunload',unload);root.remove();}
  };
}

export function mountPaperPreview(container,{bundle,api,draftNamespace='question-editor-v1'}={}) {
  if(bundle?.schema_version!==1)throw Error('仅支持 schema_version: 1。');
  let model=clone(bundle),dead=false,refreshSequence=0;const instances=new Map(),controller=new AbortController();
  const root=el('div','qe-paper'),header=el('header','qe-paper-header'),heading=el('h1','',model.paper.title),subtitle=el('p','qe-hint','整卷展开。点击文字、公式或图片编辑，逐题保存。'),actions=el('div','qe-toolbar'),status=el('p','qe-message'),body=el('main','qe-paper-body');status.setAttribute('role','status');
  root.append(header,body);header.append(el('span','qe-eyebrow','化学题卷 · 内容编辑'),heading,subtitle,actions,status);container.append(root);
  const exportBtn=button('导出已保存整卷',async()=>{
    if([...instances.values()].some(i=>i.isDirty()||i.isSaving())){status.textContent='还有未保存修改，请逐题保存或取消后再导出。';return;}exportBtn.disabled=true;status.textContent='正在准备已保存版本的导出…';
    try{const expected_revisions=Object.fromEntries(model.questions.map(q=>[q.id,q.revision]));const result=await api.exportPaper(model.paper.id,{expected_revisions,options:{}},{signal:controller.signal});if(dead)return;const url=safePreviewUrl(result.download_url);if(!url)throw Error('导出链接无效。');const a=el('a','qe-download','下载导出文件');a.href=url;a.download='';status.replaceChildren(el('span','',result.report?.mock?'模拟导出只返回版本快照 JSON，尚未生成 Word。':'已生成导出文件。'),a);}catch(e){if(!dead)status.textContent=e.message;}finally{if(!dead)exportBtn.disabled=false;}
  },'qe-primary');
  actions.append(exportBtn,button('下载当前整卷草稿',()=>downloadJson(getBundle(true),`${model.paper.id}-draft.json`)),button('读取最新已保存整卷',async()=>{
    if([...instances.values()].some(i=>i.isDirty()||i.isSaving())){status.textContent='请先保存或取消各题修改，再读取最新整卷。';return;}
    const sequence=++refreshSequence;try{const latest=await api.getPaper(model.paper.id,{signal:controller.signal});if(dead||sequence!==refreshSequence)return;if([...instances.values()].some(i=>i.isDirty()||i.isSaving())){status.textContent='读取期间有新的编辑，已保留草稿。请先保存或取消再更新。';return;}if(latest.schema_version!==1||latest.paper.id!==model.paper.id)throw Error('整卷响应无效。');model=clone(latest);mount();status.textContent='整卷已更新到最新保存版本。';}catch(e){if(!dead)status.textContent=e.message;}
  }));
  function mount(){instances.forEach(i=>i.destroy());instances.clear();body.replaceChildren();const questions=new Map(model.questions.map(q=>[q.id,q]));for(const id of model.paper.question_ids){const q=questions.get(id);if(!q){body.append(el('p','qe-warning',`题目 ${id} 缺失，请核对导入结果。`));continue;}const host=el('div');host.id=`question-${q.number || id}`;body.append(host);instances.set(id,mountQuestionEditor(host,{question:q,assets:model.assets,api,draftNamespace,onSaved:(saved,assets)=>{const index=model.questions.findIndex(q=>q.id===saved.id);model.questions[index]=saved;const assetMap=new Map(model.assets.map(a=>[a.id,a]));assets.forEach(a=>assetMap.set(a.id,a));model.assets=[...assetMap.values()];}}));}}
  function getBundle(includeDrafts=false){const result=clone(model);if(includeDrafts)result.questions=result.questions.map(q=>instances.get(q.id)?.getDraft()||q);return result;}
  mount();return {getBundle,editors:instances,destroy:()=>{dead=true;controller.abort();instances.forEach(i=>i.destroy());instances.clear();root.remove();}};
}
