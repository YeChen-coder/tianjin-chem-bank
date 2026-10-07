"""Test billing-sensitive UI guards with source functions and fake elements.

No browser or network is used. Node is optional on other developer machines.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app


def function(name):
    return re.search(r'(?:async )?function ' + name + r'\([^\n]*\) \{[\s\S]*?\n\}', app.PAGE).group(0)


def run_node(code):
    node = os.environ.get('CHEM_TEST_NODE') or shutil.which('node')
    if not node:
        pytest.skip('Node required for UI guard checks')
    result = subprocess.run([node, '-'], input=code, encoding='utf-8', capture_output=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


def test_paper_bulk_hidden_in_bank_and_separate_confirmed_bank_button_visible():
    source = function('answerPaperId') + '\n' + function('syncAiCompose')
    run_node('const source = ' + json.dumps(source) + r''';
const assert = require('node:assert/strict');
const apply = new Function('document', 'currentPaperId', 'paperFilter', source + '\nsyncAiCompose();');
for (const [current, filter, visible] of [[null,null,false], [77,null,false], [77,{id:88},true]]) {
  const elements = Object.fromEntries(['ai-compose','ai-answers','ai-intensity-wrap','answer-msg','ai-bank-answers','bank-answer-msg'].map(id => [id, {}]));
  apply({getElementById: id => elements[id]}, current, filter);
  assert.equal(elements['ai-answers'].hidden, !visible);
  assert.equal(elements['answer-msg'].hidden, !visible);
  assert.equal(elements['ai-bank-answers'].hidden, visible);
  assert.equal(elements['bank-answer-msg'].hidden, visible);
}
''')


def test_bulk_click_guard_does_not_submit_from_bank_view():
    handler = re.search(r"document.getElementById\('ai-answers'\).onclick = async \(\) => \{[\s\S]*?\n\};", app.PAGE).group(0)
    source = function('answerPaperId') + '\n' + handler
    run_node('const source = ' + json.dumps(source) + r''';
const assert = require('node:assert/strict');
const bind = new Function('document','currentPaperId','paperFilter','fetch','answerStartPoll','answerRefresh',
  source + "\nreturn document.getElementById('ai-answers').onclick;");
(async () => {
  for (const [current, filter, expected] of [[77,null,[]], [77,{id:88},['/api/papers/88/ai-answers']]]) {
    const elements = {'ai-answers':{}, 'answer-msg':{}};
    const requests = [];
    const fetch = async url => {requests.push(url); return {ok:true,json:async()=>({jobs:[],skipped:0})};};
    await bind({getElementById:id=>elements[id]},current,filter,fetch,()=>{},async()=>{})();
    assert.deepEqual(requests,expected);
  }
})().catch(error => {console.error(error);process.exitCode=1;});
''')


def test_bank_single_button_is_visible_without_opening_answer_details_and_only_posts_one_id():
    source = function('answerPaperId') + '\n' + function('mountAnswerPanel')
    run_node('const source = ' + json.dumps(source) + r''';
const assert = require('node:assert/strict');
class Element {
  constructor(tag) {this.tag=tag;this.children=[];this.dataset={};this.classList={toggle:()=>{}};this._text='';}
  set textContent(value) {this._text=value;this.children=[];}
  get textContent() {return this._text;}
  append(...nodes) {this.children.push(...nodes);}
  setAttribute() {}
}
const document = {createElement:tag=>new Element(tag)};
const bind = new Function('document','paperFilter','fetch','answerStartPoll','answerRefresh','aiFriendlyError',
  source+'\nreturn mountAnswerPanel;');
(async () => {
  const requests=[];
  const fetch=async (url, options)=>{requests.push([url,JSON.parse(options.body)]);return {ok:true,json:async()=>({jobs:[{id:1}],skipped:0})};};
  const mount=bind(document,null,fetch,()=>{},async()=>{},message=>message);
  const holder=new Element('div');
  mount(holder,{id:42,answer_state:{answer:'',revision:'r1',parts:[],has_answer:false,job:null}});
  const panel=holder.children.find(n=>n.className==='answer-panel');
  const actions=holder.children.find(n=>n.className==='answer-actions');
  assert.equal(panel.open,undefined);
  assert.equal(actions.children.length,1);
  assert.equal(actions.children[0].textContent,'AI生成答案');
  await actions.children[0].onclick();
  assert.deepEqual(requests,[['/api/questions/42/ai-answer',{}]]);
  const answered=new Element('div');
  mount(answered,{id:43,answer_state:{answer:'已有答案',revision:'r1',parts:[],has_answer:true,job:null}});
  assert.equal(answered.children.find(n=>n.className==='answer-actions').children.length,0);
  const working=new Element('div');
  mount(working,{id:44,answer_state:{answer:'',revision:'r1',parts:[],has_answer:false,job:{status:'queued',message:'等待开始'}}});
  assert.equal(working.children.find(n=>n.className==='answer-actions').children[0].disabled,true);
})().catch(error=>{console.error(error);process.exitCode=1;});
''')


def paper_harness():
    source = '\n'.join(function(name) for name in (
        'syncPaperControls', 'setPaperHint', 'setPaperViewLine', 'loadPapers', 'openPaper', 'exitPaper'))
    source += '\n' + re.search(r"document.getElementById\('newpaper'\)\.onclick =[^;]+;", app.PAGE).group(0)
    return 'const source = ' + json.dumps(source) + r''';
const assert = require('node:assert/strict');
class Element {
  constructor(tag) {
    this.tag=tag;this.children=[];this.dataset={};this.handlers={};this.attributes={};this.className='';this.value='';
    this.classList={contains:name=>this.className.split(/\s+/).includes(name),toggle:(name,on)=>{
      const names=new Set(this.className.split(/\s+/).filter(Boolean));
      if(on) names.add(name);else names.delete(name);this.className=[...names].join(' ');
    }};
  }
  set textContent(value) {this._text=value;this.children=[];}
  get textContent() {return this._text||'';}
  appendChild(node) {this.children.push(node);}
  addEventListener(name,handler) {this.handlers[name]=handler;}
  setAttribute(name,value) {this.attributes[name]=value;}
  removeAttribute(name) {delete this.attributes[name];}
}
function createUI(deferred=new Map()) {
  const elements=Object.fromEntries(['paperlist','newpaper','papername','paperhint','paperview'].map(id=>[id,new Element('div')]));
  const document={getElementById:id=>elements[id],createElement:tag=>new Element(tag),
    querySelectorAll:()=>elements.paperlist.children.filter(n=>n.classList.contains('paper'))};
  const papers=[{id:11,name:'甲卷',ids:[1,2],count:2},{id:22,name:'乙卷',ids:[3],count:1}];
  const requests=[];
  const fetch=async url=>{
    requests.push(url);
    if(deferred.has(url)) return deferred.get(url);
    return {ok:true,json:async()=>url==='/api/papers'?{papers}:papers.find(p=>url==='/api/papers/'+p.id)};
  };
  const bind=new Function('document','fetch', `
    let currentPaperId=null,paperFilter=null,openPaperIds=[],offset=0,paperOpenRequest=0;
    const picked=new Set();
    function updatePicked() {} function syncAiCompose() {} function rememberPaper() {}
    async function load() {}
    ${source}
    return {loadPapers,openPaper,exitPaper,picked,state:()=>({currentPaperId,paperFilter,openPaperIds,offset})};
  `);
  return {ui:bind(document,fetch),elements,requests,
    button:id=>document.querySelectorAll().find(n=>n.dataset.paperId===String(id))};
}
'''


def test_single_click_enters_and_reclick_stays_with_one_selected_paper_then_exit_clears_state():
    run_node(paper_harness() + r'''
(async()=>{
  const {ui,elements,button,requests}=createUI();
  await ui.loadPapers();
  assert.equal(button(11).handlers.dblclick,undefined);
  const entering=button(11).handlers.click();
  assert.ok(requests.includes('/api/papers/11')); // No timer or second click required.
  await entering;
  assert.equal(ui.state().paperFilter.id,11);
  assert.deepEqual(ui.state().paperFilter.ids,[1,2]);
  assert.equal(button(11).classList.contains('on'),true);
  assert.equal(button(22).classList.contains('on'),false);
  assert.equal(elements.newpaper.classList.contains('paper-exit-active'),true);
  ui.picked.delete(2);
  const count=requests.filter(url=>url==='/api/papers/11').length;
  await button(11).handlers.click();
  assert.equal(ui.state().paperFilter.id,11);
  assert.equal(ui.picked.has(2),false);
  assert.equal(requests.filter(url=>url==='/api/papers/11').length,count);
  await button(22).handlers.click();
  assert.equal(ui.state().paperFilter.id,22);
  assert.equal(button(11).classList.contains('on'),false);
  assert.equal(button(22).classList.contains('on'),true);
  assert.equal(elements.newpaper.classList.contains('paper-exit-active'),true);
  elements.newpaper.onclick();
  assert.equal(ui.state().currentPaperId,null);
  assert.equal(ui.state().paperFilter,null);
  assert.equal(elements.newpaper.classList.contains('paper-exit-active'),false);
  assert.equal(button(11).classList.contains('on'),false);
  assert.equal(button(22).classList.contains('on'),false);
  assert.equal(button(22).attributes['aria-current'],undefined);
  await ui.openPaper(11);
  elements.paperview.children.find(n=>n.tag==='button').handlers.click();
  assert.equal(ui.state().currentPaperId,null);
  assert.equal(elements.newpaper.classList.contains('paper-exit-active'),false);
})().catch(error=>{console.error(error);process.exitCode=1;});
''')


def test_late_paper_open_cannot_override_the_last_selection_or_reopen_after_exit():
    run_node(paper_harness() + r'''
(async()=>{
  let release11,release33;
  const deferred=new Map([
    ['/api/papers/11',new Promise(resolve=>{release11=resolve;})],
    ['/api/papers/33',new Promise(resolve=>{release33=resolve;})]
  ]);
  const {ui,elements,button}=createUI(deferred);
  await ui.loadPapers();
  const old=ui.openPaper(11);
  await ui.openPaper(22);
  release11({ok:true,json:async()=>({id:11,name:'甲卷',ids:[1,2]})});await old;
  assert.equal(ui.state().currentPaperId,22);
  assert.equal(button(22).classList.contains('on'),true);
  let releaseSwitch;
  deferred.set('/api/papers/11',new Promise(resolve=>{releaseSwitch=resolve;}));
  const switching=ui.openPaper(11);
  await ui.openPaper(22); // Clicking the current paper cancels an older pending switch.
  releaseSwitch({ok:true,json:async()=>({id:11,name:'甲卷',ids:[1,2]})});await switching;
  assert.equal(ui.state().currentPaperId,22);
  const pending=ui.openPaper(33);
  ui.exitPaper();
  release33({ok:true,json:async()=>({id:33,name:'延迟的卷子',ids:[4]})});await pending;
  assert.equal(ui.state().currentPaperId,null);
  assert.equal(ui.state().paperFilter,null);
  assert.equal(elements.newpaper.classList.contains('paper-exit-active'),false);
})().catch(error=>{console.error(error);process.exitCode=1;});
''')


def test_late_question_list_cannot_replace_the_current_paper_contents():
    run_node('const source = ' + json.dumps(function('load')) + r''';
const assert=require('node:assert/strict');
const elements=Object.fromEntries(['pageinfo','stats','pageall','pagetext'].map(id=>[id,{}]));
const list={children:[],set innerHTML(value){this.children=[];},appendChild(item){this.children.push(item);}};
let release;
const delayed=new Promise(resolve=>{release=resolve;});
const fetch=async url=>url.searchParams.get('ids')==='1'?delayed:
  {json:async()=>({items:[{id:2}],total:1,stats_line:'乙卷'})};
const bind=new Function('fetch','document','list',`
  let questionLoadRequest=0,paperFilter=null,offset=0,total=0;const limit=20;
  const location={origin:'http://unit.test'},window={};
  function renderQuestion(item){return item;} function paintAllPicks(){}
  ${source}
  return {load,setPaper:p=>{paperFilter=p;}};
`);
(async()=>{
  const ui=bind(fetch,{getElementById:id=>elements[id]},list);
  ui.setPaper({id:11,ids:[1]});const first=ui.load();
  ui.setPaper({id:22,ids:[2]});await ui.load();
  release({json:async()=>({items:[{id:1}],total:1,stats_line:'旧甲卷'})});await first;
  assert.deepEqual(list.children,[{id:2}]);assert.equal(elements.stats.textContent,'乙卷');
})().catch(error=>{console.error(error);process.exitCode=1;});
''')


def test_bank_modal_cancellation_wrong_text_and_reopening_never_start_tasks():
    source = function('mountBankAnswerConfirmation')
    run_node('const source = ' + json.dumps(source) + r''';
const assert=require('node:assert/strict');
const elements=Object.fromEntries(['ai-bank-answers','bank-answer-confirm','bank-answer-agreement','bank-answer-start','bank-answer-msg','bank-answer-cancel','bank-answer-form'].map(id=>[id,{}]));
const dialog=elements['bank-answer-confirm'],input=elements['bank-answer-agreement'],start=elements['bank-answer-start'];
dialog.showModal=()=>{dialog.open=true;};dialog.close=()=>{dialog.open=false;};input.focus=()=>{};
const requests=[];
const fetch=async(url,options)=>{requests.push([url,JSON.parse(options.body)]);return {ok:true,json:async()=>({queued:2,already_running:0,skipped:1})};};
const bind=new Function('document','fetch',`let bankAnswerSubmitting=false;
function answerPaperId(){return null;}function answerStartPoll(){}async function answerRefresh(){}
${source}\nmountBankAnswerConfirmation();`);
bind({getElementById:id=>elements[id]},fetch);
(async()=>{
  const event={preventDefault(){}};
  elements['ai-bank-answers'].onclick();
  assert.equal(dialog.open,true);assert.equal(start.disabled,true);assert.equal(requests.length,0);
  for(const value of ['', '同意', '我同意 ', '我同意但不开始']) {
    input.value=value;input.oninput();assert.equal(start.disabled,true);
    await elements['bank-answer-form'].onsubmit(event);assert.equal(requests.length,0);
  }
  input.value='我同意';input.oninput();assert.equal(start.disabled,false);
  elements['bank-answer-cancel'].onclick();
  await elements['bank-answer-form'].onsubmit(event);assert.equal(requests.length,0);
  elements['ai-bank-answers'].onclick();assert.equal(input.value,'');assert.equal(start.disabled,true);
  input.value='我同意';input.oninput();await elements['bank-answer-form'].onsubmit(event);
  assert.deepEqual(requests,[['/api/answers/bank-generate',{confirmation:'我同意'}]]);
  assert.equal(dialog.open,false);
  await elements['bank-answer-form'].onsubmit(event);assert.equal(requests.length,1);
  elements['ai-bank-answers'].onclick();assert.equal(input.value,'');assert.equal(start.disabled,true);
})().catch(error=>{console.error(error);process.exitCode=1;});
''')


def test_bank_progress_keeps_button_disabled_until_finished_or_stopped():
    source = function('bankAnswerRefresh')
    run_node('const source = ' + json.dumps(source) + r''';
const assert=require('node:assert/strict');
const elements={'ai-bank-answers':{},'bank-answer-msg':{}};
let current={batch:{active:50,done:1,skipped:10,cancelled:0,failed:0}};
const bind=new Function('document','fetch',`let bankAnswerSubmitting=false;${source}\nreturn bankAnswerRefresh;`);
const refresh=bind({getElementById:id=>elements[id]},async()=>({ok:true,json:async()=>current}));
(async()=>{
  assert.equal(await refresh(),true);assert.equal(elements['ai-bank-answers'].disabled,true);
  assert.ok(elements['bank-answer-msg'].textContent.includes('50'));
  current={batch:{active:0,done:1,skipped:10,cancelled:50,failed:0}};
  assert.equal(await refresh(),false);assert.equal(elements['ai-bank-answers'].disabled,false);
  assert.ok(elements['bank-answer-msg'].textContent.includes('叫停'));
})().catch(error=>{console.error(error);process.exitCode=1;});
''')
