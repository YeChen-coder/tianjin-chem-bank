const {chromium}=require('playwright'),assert=require('assert').strict,fs=require('fs'),path=require('path');
const [base,out]=process.argv.slice(2);
(async()=>{
 const browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
 try{
  await page.goto(base+'/static/question_editor_v2/demo.html?paper=imported-paper');await page.waitForFunction(()=>window.editorDemo);await page.evaluate(()=>document.fonts.ready);
  const before=await page.evaluate(()=>editorDemo.mounted.getBundle());const q=before.questions.find(q=>q.body.some(p=>p.inlines?.some(n=>n.type==='formula')));
  const card=page.locator(`[data-question-id="${q.id}"]`);const formula=q.body.flatMap(b=>b.inlines||[]).find(n=>n.type==='formula');
  const compiled=page.waitForResponse(r=>r.url().endsWith('/formulas/compile'));
  await card.locator(`[data-node-id="${formula.id}"]`).click();await page.locator('dialog math-field').evaluate(f=>{f.value='\\mathrm{NaOH}';f.dispatchEvent(new Event('input',{bubbles:true}));});await page.getByRole('button',{name:'检查并应用到草稿'}).click();const response=await compiled;assert.equal(response.status(),200);const compile=await response.json();assert.ok(compile.omml);assert.ok(!compile.compiler_version.startsWith('mock'));await page.waitForFunction(()=>!document.querySelector('dialog'));
  await card.getByRole('button',{name:'保存本题',exact:true}).click();await page.waitForFunction(id=>!editorDemo.mounted.editors.get(id).isDirty(),q.id);const saved=await page.evaluate(id=>editorDemo.mounted.editors.get(id).getSaved(),q.id);assert.equal(saved.revision,q.revision+1);assert.equal(saved.id,q.id);assert.deepEqual(saved.source,q.source);assert.deepEqual(saved.options,q.options);assert.deepEqual(saved.answers,q.answers);
  await page.reload();await page.waitForFunction(()=>window.editorDemo);const restored=await page.evaluate(id=>editorDemo.mounted.editors.get(id).getSaved(),q.id);assert.deepEqual(restored,saved);
  const [download]=await Promise.all([page.waitForEvent('download'),(async()=>{await page.getByRole('button',{name:'导出已保存整卷',exact:true}).click();await page.waitForSelector('.qe-download');await page.locator('.qe-download').click();})()]);await download.saveAs(path.join(out,'browser-edited-paper.docx'));assert.ok(fs.statSync(path.join(out,'browser-edited-paper.docx')).size>1000);
  await page.screenshot({path:path.join(out,'real-api-desktop.png')});
  fs.writeFileSync(path.join(out,'before.json'),JSON.stringify(before,null,2));fs.writeFileSync(path.join(out,'after.json'),JSON.stringify(await page.evaluate(()=>editorDemo.mounted.getBundle()),null,2));assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(out,'browser-integration-report.json'),JSON.stringify({realApi:true,sourcePaperId:before.paper.id,questionId:q.id,sourceRevision:q.revision,savedRevision:saved.revision,compilerVersion:compile.compiler_version,checks:['MathLive -> production compile API','Atomic saved revision','IDs/source/options/answers retained','Reload reads actual ContentStore','Saved snapshot -> actual DOCX download'],errors,wordVisualVerified:false},null,2));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
