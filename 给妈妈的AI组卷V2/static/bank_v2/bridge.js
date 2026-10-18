import {mountQuestionEditor,createContentApi} from '/static/question_editor_v2/editor.js';
import {renderContent,el,button} from '/static/question_editor_v2/renderer.js';
const api=createContentApi();let ui;const editors=new Map(),cards=new Map(),timers=new Set();
const status=()=>document.getElementById('bank-status');
function notice(text,error=false){status().textContent=text;status().classList.toggle('error',error);}
async function request(path,body){const r=await fetch('/api/v2/content/'+path,{method:body===undefined?'GET':'POST',headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});const data=await r.json();if(!r.ok)throw Error(data.error?.message||'操作未完成');return data;}
export function guard(){if([...editors.values()].some(e=>e.isDirty()||e.isSaving())){notice('请先保存或取消题卡里的修改。',true);return false;}return true;}
export function disposeEditors(){for(const e of editors.values())e.destroy();editors.clear();cards.clear();for(const t of timers)clearTimeout(t);timers.clear();}
export function renderStem(host,item){
  host.classList.add('bank-content');const q=item.structured.question;
  renderContent(host,{...q,answers:[],explanations:[]},item.structured.assets);
  host.querySelectorAll('.qe-formula').forEach(f=>{f.classList.add('bank-read-formula');f.tabIndex=0;f.title='点击编辑公式';const open=()=>{const holder=host.closest('.chk')?.lastElementChild||host.parentElement;edit(holder,host,item,f.dataset.nodeId);};f.addEventListener('click',open);f.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();open();}});});
}
export function mountAnswers(holder,item){
  const q=item.structured.question;const panel=el('details','answer-panel');panel.append(el('summary','','答案 / 解析'));
  const content=el('div','bank-content');panel.append(content);
  if(q.answers.length||q.explanations.length){renderContent(content,{...q,body:[],options:[],issues:[]},item.structured.assets);content.querySelectorAll(':scope > .qe-subquestion').forEach(n=>n.remove());}
  else content.append(el('p','answer-note','尚未填写，可从铅笔入口添加答案和解析。'));
  holder.append(panel);
}
export function edit(holder,stem,item,nodeId){
  const qid=item.structured.question.id;const existing=editors.get(qid);
  if(existing){if(nodeId)holder.querySelector(`[data-node-id="${CSS.escape(nodeId)}"].qe-selectable`)?.click();return;}
  const box=el('div','bank-editor');holder.append(box);stem.hidden=true;
  const close=button('收起编辑',()=>{if(editor.isDirty()||editor.isSaving()){notice('请先保存或取消本题修改。',true);return;}editor.destroy();editors.delete(qid);box.remove();stem.hidden=false;},'secondary bank-editor-close');box.append(close);
  const editor=mountQuestionEditor(box,{question:item.structured.question,assets:item.structured.assets,api,onSaved:(q,assets)=>{item.structured={schema_version:1,question:q,assets};item.qtype=q.qtype;renderStem(stem,item);holder.querySelector('.answer-panel')?.remove();mountAnswers(holder,item);const card=cards.get(item.id);if(card){card.item.structured=item.structured;refreshCandidates(card).catch(e=>card.state.textContent=e.message);}notice('本题已保存，导出会使用这个版本。');}});
  editors.set(qid,editor);
  if(nodeId)box.querySelector(`[data-node-id="${CSS.escape(nodeId)}"].qe-selectable`)?.click();
}
export function split(holder,item){
  const q=item.structured.question;
  if(q.body.length<2){notice('此题只有一个题干段落。请先在编辑中用段落设置拆出题目边界，再分隔。',true);return;}
  if(holder.querySelector('.bank-split'))return;
  const box=el('div','splitedit bank-split');box.append(el('p','','选择第二道题从哪一段开始：'));
  const select=el('select');q.body.slice(1).forEach((b,index)=>{const o=el('option','',`从第 ${index+2} 段开始：`+(b.inlines?.map(n=>n.text||n.latex||'').join('')||b.type).slice(0,60));o.value=String(index+1);select.append(o);});box.append(select);
  box.append(button('分成两道题',async()=>{if(!guard())return;try{const result=await request(`bank/questions/${item.id}/split`,{expected_revision:q.revision,at:Number(select.value),paper_id:ui.paperId()});notice('已分成两道题。');if(result.paper)ui.applyPaper(result.paper);await ui.refresh();}catch(e){notice(e.message,true);}}),button('取消',()=>box.remove(),'secondary'));holder.append(box);
}
export async function importFile(e){
  const file=e.target.files[0];if(!file)return;if(!guard()){e.target.value='';return;}
  e.target.disabled=true;notice('正在读取 Word、恢复公式并整理题目…');
  try{const content=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(String(r.result).split(',')[1]);r.onerror=reject;r.readAsDataURL(file);});const result=await request('import',{name:file.name,content_base64:content});await ui.refreshPapers();await ui.refreshTree();await ui.openPaper(result.paper.id);notice(result.duplicate?'这份原卷已导入，已打开保留的编辑版本。':'Word 已导入，可以直接勾选、编辑和组卷。');}catch(e){notice(e.message,true);}finally{e.target.disabled=false;e.target.value='';}
}
export async function exportSelection(ids,title){
  if(!guard())return;notice('正在生成 Word…');
  try{const contents=await Promise.all(ids.map(async id=>{const r=await fetch('/api/questions/'+id);const item=await r.json();if(!r.ok||!item.structured)throw Error('题目尚未准备好，请刷新后导出。');return item.structured.question;}));
    const inline=document.getElementById('keepanswers').checked,end=document.getElementById('answersatend').checked;
    const result=await request('bank/selection/export',{ids,title,expected_revisions:Object.fromEntries(contents.map(q=>[q.id,q.revision])),keep_source:document.getElementById('keepsrc').checked,options:{auto_number:document.getElementById('autonum').checked,include_answers:inline||end,include_explanations:inline||end,answers_position:inline?'inline':'end'}});
    const a=el('a','','下载 Word');a.href=result.download_url;a.download=(title||'化学试卷')+'.docx';status().replaceChildren(document.createTextNode('Word 已生成。 '),a);a.click();
  }catch(e){notice(e.message,true);}
}
async function refreshCandidates(card){
  if(!card.host.isConnected)return;
  const data=await request(`bank/questions/${card.item.id}/candidates`);
  if(!card.host.isConnected)return;
  const mounted=[...editors.keys()].filter(id=>card.versions.querySelector(`[data-question-id="${CSS.escape(id)}"]`));
  const dirty=mounted.some(id=>editors.get(id).isDirty()||editors.get(id).isSaving());
  if(!dirty){for(const id of mounted){editors.get(id).destroy();editors.delete(id);}card.versions.replaceChildren();for(const [index,row] of [...data.items].reverse().entries())renderCandidate(card,row,index+1);}
  const active=data.jobs.length>0;card.generate.disabled=active;card.cancel.hidden=!active;card.jobs=data.jobs;
  if(active){card.state.textContent='正在改写并独立核对答案…';const timer=setTimeout(()=>{timers.delete(timer);refreshCandidates(card).catch(e=>card.state.textContent=e.message);},2000);timers.add(timer);}
  else if(card.jobsFinished){card.state.textContent='改写完成，请核对下方候选。';}
}
function renderCandidate(card,row,seq){
  const q=row.content.question,source=card.item.structured.question;const blocked=row.status==='candidate_rejected';const stale=source.revision!==row.source_revision;
  const box=el('div','aiver');const line=el('div','ailine');const selected=el('input');selected.type='checkbox';selected.title='放入这套试卷';selected.disabled=blocked||stale||!ui.paperId();
  const label=el('label','keep');label.append(selected,document.createTextNode('放入这套试卷'));
  line.append(label,el('b','',`V${seq}`),el('span','aistat '+(blocked?'fail':stale?'suspect':'pass'),blocked?'未通过核对':stale?'母题已修改，请重新生成':'已核对 · 请老师确认'));
  box.append(line);const body=el('div','stem');const candidateItem={id:row.id,structured:row.content};renderStem(body,candidateItem);box.append(body);mountAnswers(box,candidateItem);
  const tools=el('div','bank-version-tools');tools.append(button('编辑这个版本',()=>edit(box,body,candidateItem),'secondary'));box.append(tools);
  const reason=row.result.judgement?.reason||row.result.judgement?.summary;
  if(reason){const detail=el('details');detail.append(el('summary','','查看核对意见'),el('p','',reason));box.append(detail);}
  const pid=ui.paperId();if(pid)fetch('/api/papers/'+pid).then(r=>r.json()).then(p=>{if(!box.isConnected)return;selected.checked=!!row.legacy_id&&(p.ids||[]).includes(row.legacy_id);box.classList.toggle('pick-on',selected.checked);}).catch(()=>{});
  selected.addEventListener('change',async()=>{if(!guard()){selected.checked=!selected.checked;return;}selected.disabled=true;try{const current=await api.getQuestion(q.id),result=await request(`bank/candidates/${row.id}/select`,{paper_id:ui.paperId(),on:selected.checked,expected_source_revision:source.revision,expected_revision:current.question.revision});ui.applyPaper(result.paper);box.classList.toggle('pick-on',selected.checked);notice(selected.checked?'这个版本已放入试卷，母题仍保留。':'这个版本已移出试卷。');}catch(e){selected.checked=!selected.checked;notice(e.message,true);}finally{selected.disabled=blocked||stale||!ui.paperId();}});
  card.versions.append(box);
}
function mountAi(item){
  const art=document.querySelector(`#list > article[data-qid="${item.id}"]`);if(!art||art.querySelector('.aibox')||item.structured.question.origin==='ai')return;
  const host=el('div','aibox'),row=el('div','aihead'),generate=button('再生成一个版本',async()=>{if(!guard())return;generate.disabled=true;try{const latest=await api.getQuestion(item.structured.question.id);await request(`bank/questions/${item.id}/ai`,{expected_revision:latest.question.revision,intensity:select.value,teacher_feedback:feedback.value});card.jobsFinished=true;await refreshCandidates(card);}catch(e){state.textContent=e.message;generate.disabled=false;}}),select=el('select');
  for(const [value,text] of [['light','轻'],['medium','中'],['deep','深']]){const o=el('option','',text);o.value=value;select.append(o);}select.value='medium';
  const cancel=button('叫停本题',async()=>{for(const j of card.jobs)await request(`jobs/${j.id}/cancel`,{});await refreshCandidates(card);state.textContent='已叫停。';},'secondary');cancel.hidden=true;row.append(generate,select,cancel);
  const feedbackPanel=el('details');feedbackPanel.append(el('summary','','给 AI 的修改意见（可选）'));const feedback=el('textarea','bank-feedback');feedback.placeholder='例如：保留三个小问，把每毫升含量改为7.0mg，并补齐计算解析。';feedback.maxLength=2000;feedbackPanel.append(feedback);
  const state=el('p','bank-ai-status','改写结果保留为候选，核对后再放入试卷。'),versions=el('div');state.setAttribute('role','status');host.append(row,feedbackPanel,state,versions);art.querySelector('.chk')?.lastElementChild.append(host);
  const card={host,item,generate,select,feedback,cancel,state,versions,jobs:[]};cards.set(item.id,card);refreshCandidates(card).catch(e=>state.textContent=e.message);
}
export function install(callbacks){
  ui=callbacks;if(globalThis.MathfieldElement){MathfieldElement.fontsDirectory='/static/mathlive/fonts';MathfieldElement.soundsDirectory=null;}
  for(const id of ['ai-answers','ai-bank-answers']){const b=document.getElementById(id);if(b){b.disabled=true;b.title='批量补答案尚未接入；请从题卡铅笔入口编辑答案与解析。';}}
  const bar=el('div');bar.id='bank-status';bar.setAttribute('role','status');document.querySelector('.bar').after(bar);
  window.chemBankMountAi=async()=>{const data=await fetch('/api/questions?ids='+[...document.querySelectorAll('#list > article')].map(a=>a.dataset.qid).join(',')).then(r=>r.json());for(const item of data.items||[])if(item.structured)mountAi(item);};
  window.chemBankAiCompose=async detail=>{if(!guard())return;notice('正在提交所选题目的改写…');let n=0;try{for(const id of detail.questionIds){const card=cards.get(id);if(!card)continue;const latest=await api.getQuestion(card.item.structured.question.id);await request(`bank/questions/${id}/ai`,{expected_revision:latest.question.revision,intensity:document.getElementById('ai-intensity').value,teacher_feedback:card.feedback.value});card.jobsFinished=true;await refreshCandidates(card);n++;}notice(`已提交 ${n} 道题，进度见各题下方。`);}catch(e){notice(e.message,true);}};
  window.addEventListener('beforeunload',e=>{if([...editors.values()].some(editor=>editor.isDirty()||editor.isSaving())){e.preventDefault();e.returnValue='';}});
}
