import {mountPaperPreview,createContentApi} from './editor.js';
const api=createContentApi();
if(globalThis.MathfieldElement){MathfieldElement.fontsDirectory='/vendor/mathlive/fonts';MathfieldElement.soundsDirectory=null;}
let mounted,loadSequence=0;
async function show(id){
  if(mounted && [...mounted.editors.values()].some(i=>i.isDirty()||i.isSaving())){document.querySelector('#demo-status').textContent='请先保存或取消当前试卷的修改再切换。';return;}
  const sequence=++loadSequence;try{const bundle=await api.getPaper(id);if(sequence!==loadSequence)return;if(mounted&&[...mounted.editors.values()].some(i=>i.isDirty()||i.isSaving())){document.querySelector('#demo-status').textContent='切换期间有新修改，已保留当前试卷。';return;}mounted?.destroy();mounted=mountPaperPreview(document.querySelector('#app'),{bundle,api,draftNamespace:'session-2-demo'});window.editorDemo={mounted,api};
    document.querySelector('#demo-scope').textContent=catalog.mode==='real-api'?'真实 API 联调：读写仅发生在会话 2 的隔离 ContentStore；公式编译和 Word 导出调用会话 4 的真实接口。此页为测试副本。':id==='imported-paper'?'本卷直接使用会话 1 的真实导入 bundle，来源和待核对项均保留。保存只写会话 2 私有测试稿，编译及导出为模拟服务。':id==='real-paper'?'真实试卷来自已验证实验的只读结构副本；分题仅供前端展示核对。保存只写私有测试稿，编译及导出为模拟服务。':'合成样例包含公式、配图、合并表格、小问、对应答案和解析。保存只写私有测试稿，编译及导出为模拟服务。';document.querySelector('#demo-status').textContent='';
  }catch(e){document.querySelector('#demo-status').textContent=e.message;}
}
for(const [id,paper] of [['synthetic','control-paper'],['real','real-paper'],['imported','imported-paper']])document.getElementById(id).addEventListener('click',()=>show(paper));
for(const [id,mode] of [['failure','save_failure'],['conflict','conflict'],['slow','slow_compile']])document.getElementById(id).addEventListener('click',async()=>{try{const r=await fetch('/mock/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode})});if(!r.ok)throw Error('模拟控制请求失败');document.querySelector('#demo-status').textContent='下一次对应请求将演示此情况，测试草稿不会写入正式题库。';}catch(e){document.querySelector('#demo-status').textContent=e.message;}});
const catalog=await (await fetch('/mock/catalog')).json();const hasImported=catalog.papers.some(p=>p.alias==='imported-paper');document.querySelector('#imported').hidden=!hasImported;
if(catalog.mode==='real-api')for(const id of ['synthetic','real','failure','conflict','slow'])document.getElementById(id).hidden=true;
const hasHistorical=catalog.papers.some(p=>p.alias==='real-paper');if(!hasHistorical)document.querySelector('#real').hidden=true;
show(new URLSearchParams(location.search).get('paper') || (hasImported?'imported-paper':hasHistorical?'real-paper':'control-paper'));
