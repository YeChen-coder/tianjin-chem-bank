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
    return re.search(r'function ' + name + r'\([^\n]*\) \{[\s\S]*?\n\}', app.PAGE).group(0)


def run_node(code):
    node = os.environ.get('CHEM_TEST_NODE') or shutil.which('node')
    if not node:
        pytest.skip('Node required for UI guard checks')
    result = subprocess.run([node, '-'], input=code, encoding='utf-8', capture_output=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


def test_bulk_hidden_in_bank_even_when_a_previous_paper_is_remembered():
    source = function('answerPaperId') + '\n' + function('syncAiCompose')
    run_node('const source = ' + json.dumps(source) + r''';
const assert = require('node:assert/strict');
const apply = new Function('document', 'currentPaperId', 'paperFilter', source + '\nsyncAiCompose();');
for (const [current, filter, visible] of [[null,null,false], [77,null,false], [77,{id:88},true]]) {
  const elements = Object.fromEntries(['ai-compose','ai-answers','ai-intensity-wrap','answer-msg'].map(id => [id, {}]));
  apply({getElementById: id => elements[id]}, current, filter);
  assert.equal(elements['ai-answers'].hidden, !visible);
  assert.equal(elements['answer-msg'].hidden, !visible);
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
