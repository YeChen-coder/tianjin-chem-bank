# -*- coding: utf-8 -*-
"""Local web app for the Tianjin grade-9 chemistry question bank."""
import json
import os
import re
import sqlite3
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(__file__))
import banklib
import aivariant
import question_analysis
import bot_bridge

HOST = "127.0.0.1"
PORT = int(os.environ.get("CHEM_PORT") or 8765)
LOCK = threading.Lock()

PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>九年级化学题库</title>
<style>
  body { font-family: "Noto Sans CJK SC", "Microsoft YaHei", sans-serif; margin: 0; background: #f4f6f8; color: #1c1c1c; }
  header { background: #0f4c81; color: #fff; padding: 14px 20px; }
  header h1 { margin: 0; font-size: 20px; font-weight: 600; }
  header p { margin: 4px 0 0; font-size: 13px; opacity: .9; }
  .bar { display: flex; flex-wrap: wrap; gap: 8px; padding: 12px 20px; background: #fff; border-bottom: 1px solid #e3e6ea; align-items: center; position: sticky; top: 0; z-index: 2; }
  input[type=search], select { padding: 7px 8px; border: 1px solid #cfd5dc; border-radius: 6px; font-size: 14px; }
  input[type=search] { min-width: 220px; flex: 1; }
  button, .btn { background: #0f4c81; color: #fff; border: 0; border-radius: 6px; padding: 7px 12px; cursor: pointer; font-size: 14px; }
  button.secondary { background: #5c6b7a; }
  .stats { padding: 8px 20px; font-size: 13px; color: #445; }
  main { padding: 8px 20px 40px; }
  article { position: relative; background: #fff; border: 1px solid #e3e6ea; border-radius: 8px; padding: 12px 14px 36px; margin: 10px 0; }
  article.pick-on { background: #e5f6ea; }
  article.pick-off { background: #fdeeee; }
  button.pencil { position: absolute; left: 8px; bottom: 8px; width: 26px; height: 26px; padding: 0; margin: 0; line-height: 24px; font-size: 15px; text-align: center; background: #f4f7fb; color: #0f4c81; border: 1px solid #c5d0dc; border-radius: 4px; }
  button.pencil:hover { background: #e4edf6; }
  button.split { position: absolute; left: 38px; bottom: 8px; width: 26px; height: 26px; padding: 0; margin: 0; line-height: 24px; font-size: 14px; text-align: center; background: #f4f7fb; color: #0f4c81; border: 1px solid #c5d0dc; border-radius: 4px; }
  button.split:hover { background: #e4edf6; }
  button.trash { position: absolute; right: 8px; bottom: 8px; width: 26px; height: 26px; padding: 0; margin: 0; line-height: 24px; font-size: 14px; text-align: center; background: #fdf6f6; color: #8a2b2b; border: 1px solid #e2c8c8; border-radius: 4px; }
  button.trash:hover { background: #f8e6e6; }
  article.has-movers { padding-top: 40px; }
  .movers { position: absolute; top: 8px; right: 8px; display: flex; gap: 4px; }
  button.move { width: 26px; height: 26px; padding: 0; margin: 0; line-height: 24px; font-size: 14px; text-align: center; background: #f4f7fb; color: #0f4c81; border: 1px solid #c5d0dc; border-radius: 4px; }
  button.move:hover { background: #e4edf6; }
  .handpanel { margin: 8px 20px 0; padding: 12px 14px; background: #fff; border: 1px solid #c5d0dc; border-radius: 8px; display: flex; flex-direction: column; gap: 8px; max-width: 760px; }
  .handpanel[hidden] { display: none !important; }
  .handpanel h2 { margin: 0; font-size: 16px; font-weight: 600; }
  .handpanel label.fld { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; font-size: 14px; }
  .handpanel input[type=text], .handpanel select, .handpanel textarea { padding: 6px 8px; border: 1px solid #cfd5dc; border-radius: 6px; font-size: 14px; font-family: inherit; }
  .handpanel textarea { width: 100%; min-height: 140px; box-sizing: border-box; resize: vertical; }
  .handpanel .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .handpanel .msg { color: #0a7a32; font-size: 13px; }
  .handpanel .hint { margin: 0; font-size: 13px; color: #556; }
  .assignlist { display: flex; flex-direction: column; gap: 4px; max-height: 240px; overflow: auto; }
  .assignlist label { display: flex; align-items: center; gap: 6px; font-size: 14px; }
  .stemedit, .splitedit { display: flex; flex-direction: column; gap: 6px; margin-top: 8px; }
  .stemedit textarea, .splitedit textarea { width: 100%; min-height: 140px; box-sizing: border-box; font: inherit; line-height: 1.5; padding: 8px; border: 1px solid #cfd5dc; border-radius: 6px; resize: vertical; }
  .splitedit textarea.full { min-height: 90px; }
  .splitedit textarea.half { min-height: 90px; }
  .stemedit .row, .splitedit .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .splitedit .lbl { font-size: 13px; color: #334; }
  .splitedit .hint { font-size: 12px; color: #667; }
  .splitedit .blocks { display: flex; flex-direction: column; gap: 6px; }
  .splitedit .block { border: 1px solid #e3e6ea; border-radius: 6px; padding: 6px; background: #fafbfc; }
  .splitedit .block.headside { border-left: 3px solid #0f4c81; }
  .splitedit .block.tailside { border-left: 3px solid #2e7d32; }
  .splitedit .cutline { display: flex; align-items: center; gap: 6px; font-size: 13px; color: #0f4c81; margin: 2px 0; }
  .splitedit .block img { max-width: 220px; max-height: 180px; display: block; background: #fff; }
  .splitedit textarea.blocktext { min-height: 72px; }
  .stemedit .blocks { display: flex; flex-direction: column; gap: 6px; }
  .stemedit .block { border: 1px solid #e3e6ea; border-radius: 6px; padding: 6px; background: #fafbfc; }
  .stemedit .block img { max-width: 220px; max-height: 180px; display: block; background: #fff; }
  .stemedit textarea.blocktext { min-height: 72px; }
  .stemedit .hint { font-size: 12px; color: #667; }
  .stemedit .imgtools { margin-top: 4px; }
  article .meta { font-size: 12px; color: #667; margin-bottom: 6px; }
  .stem { white-space: pre-wrap; line-height: 1.55; font-size: 15px; }
  .answerblank { display: inline-block; border-bottom: 1px solid currentColor; height: 1em; vertical-align: baseline; max-width: 100%; }
  .stem table { border-collapse: collapse; background: #fff; margin: 6px 0; max-width: 100%; }
  .stem td { border: 1px solid #b7b7b7; padding: 4px 8px; vertical-align: top; white-space: pre-wrap; }
  label.keep { display: flex; align-items: center; gap: 6px; font-size: 14px; color: #223; }
  .stem img, article img { max-width: 220px; max-height: 180px; vertical-align: middle; margin: 4px; background: #fff; border: 1px solid #eee; }
  .src { font-size: 12px; color: #555; margin-top: 8px; }
  .src b { color: #333; }
  details { margin-top: 6px; font-size: 13px; color: #333; }
  .pager { display: flex; gap: 8px; align-items: center; padding: 8px 0; }
  label.chk, .chk { display: flex; gap: 8px; align-items: flex-start; }
  .empty { color: #777; padding: 24px; }
  .types { flex: 1 0 100%; display: flex; flex-wrap: wrap; gap: 8px 14px; align-items: center; padding: 6px 0 2px; font-size: 14px; }
  .types label { display: flex; align-items: center; gap: 4px; cursor: pointer; }
  .catedit { display: flex; flex-direction: column; align-items: flex-start; gap: 4px; margin: 2px 0 8px; font-size: 13px; width: 100%; }
  .pickrow { display: flex; flex-wrap: wrap; gap: 4px 12px; align-items: center; width: 100%; }
  .pickrow b { color: #223; }
  .pickrow label, .newpick { display: inline-flex; align-items: center; gap: 3px; cursor: pointer; }
  .newbox { display: none; gap: 6px; align-items: center; }
  .catedit input[type=text] { padding: 4px 6px; border: 1px solid #cfd5dc; border-radius: 6px; font-size: 13px; width: 140px; }
  .catedit .msg { color: #0a7a32; font-size: 12px; }
  .qtypebar { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; font-size: 14px; color: #223; margin: 2px 0 8px; }
  .qtypebar select.qtype { padding: 4px 6px; }
  .qtypebar .msg { color: #0a7a32; font-size: 12px; }
  #papername { padding: 7px 8px; border: 1px solid #cfd5dc; border-radius: 6px; font-size: 14px; min-width: 220px; }
  .papers { padding: 4px 20px 0; }
  .papers h2 { font-size: 15px; margin: 8px 0 6px; font-weight: 600; }
  .paperlist { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
  .paperlist .empty { padding: 0; }
  button.paper.on { outline: 2px solid #f0c14a; }
  #paperhint { font-size: 13px; color: #223; }
  .paperview { margin: 8px 20px 0; padding: 8px 10px; background: #fff8e6; border: 1px solid #f0c14a; border-radius: 6px; font-size: 14px; color: #223; display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
  .paperview[hidden] { display: none !important; }
  .aibox { margin-top: 10px; border-top: 1px dashed #d5dde6; padding-top: 8px; }
  .aihead, .ailine, .airedit .row, .aibox .row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
  .ainote, .aiprof { font-size: 12px; color: #556; margin: 4px 0; }
  .aiprogress { margin: 8px 0; padding: 10px 12px; background: #eef5fc; border-left: 3px solid #0f4c81; color: #234; line-height: 1.6; }
  .aiprogress[hidden] { display: none; }
  .aiver { position: relative; background: #f7fafc; border: 1px solid #e1e7ee; border-radius: 6px; padding: 8px 8px 36px; margin: 8px 0; }
  .aiver.pick-on { background: #e5f6ea; }
  .aiver.pick-off { background: #fdeeee; }
  .aistop { font-size: 13px; color: #8a5a00; font-weight: 600; margin: 6px 0; }
  .aiver .stem { font-size: 14px; }
  .airedit { display: flex; flex-direction: column; gap: 6px; margin-top: 6px; }
  .airedit textarea { width: 100%; min-height: 52px; box-sizing: border-box; font: inherit; padding: 6px; border: 1px solid #cfd5dc; border-radius: 6px; }
  .aistat.pass { color: #0a7a32; font-weight: 600; }
  .aistat.suspect { color: #8a5a00; font-weight: 600; }
  .aistat.fail { color: #8a2b2b; font-weight: 600; }
  .aistat.run { color: #0f4c81; }
  #ai-msg { font-size: 13px; color: #223; }
</style>
</head>
<body>
<header>
  <h1>九年级化学题库</h1>
  <p>天津 · 个人题库 · 勾选题目后可导出 Word 试卷</p>
</header>
<div class="bar">
  <input id="q" type="search" placeholder="搜索题干、来源文件、原题号…"/>
  <select id="major"><option value="">全部大类</option></select>
  <select id="minor"><option value="">全部小类</option></select>
  <select id="theme" aria-label="课标学习主题"><option value="">全部课标主题</option></select>
  <select id="knowledge" aria-label="知识点建议"><option value="">全部知识点建议</option></select>
  <button id="search" type="button">筛选</button>
  <button id="export" type="button">把勾选好的题目导出为word</button>
  <input id="papername" type="text" maxlength="80" placeholder="试卷名称，留空保存为未命名试卷"/>
  <button id="makepaper" type="button">从0组卷</button>
  <button id="assignpapers" type="button">归入已有试卷</button>
  <button id="exportpaper" class="secondary" type="button">导出这套为Word</button>
  <button id="newpaper" class="secondary" type="button">退出当前试卷</button>
  <span id="paperhint"></span>
  <label class="keep"><input id="keepsrc" type="checkbox"/> 导出时保留来源</label>
  <label class="keep"><input id="autonum" type="checkbox" checked/> 导出时自动编号</label>
  <label class="keep" title="默认不带答案；勾选后只附上题库已有的答案，没有答案的题保持原样"><input id="keepanswers" type="checkbox"/> 导出时附带已有答案（默认不带）</label>
  <label class="btn secondary" style="display:inline-block">通过 Word 文档导入题目
    <input id="file" type="file" accept=".doc,.docx" style="display:none"/>
  </label>
  <button id="handwrite" type="button">手写单一题目</button>
  <span id="picked">已选 0 题</span>
  <div class="types" id="types">
    <span>题型</span>
    <label><input type="checkbox" name="qtype" value="单选题"/> 单选题</label>
    <label><input type="checkbox" name="qtype" value="多选题"/> 多选题</label>
    <label><input type="checkbox" name="qtype" value="填空题"/> 填空题</label>
    <label><input type="checkbox" name="qtype" value="简答题"/> 简答题</label>
    <label><input type="checkbox" name="qtype" value="实验题"/> 实验题</label>
    <label><input type="checkbox" name="qtype" value="计算题"/> 计算题</label>
    <span style="color:#667">不选就是全部，可以同时选几种</span>
  </div>
</div>
<div id="assignpanel" class="handpanel" hidden>
  <h2>归入已有试卷</h2>
  <p class="hint">勾选要并入的试卷，可多选。已在卷上的题目不会重复。</p>
  <div id="assignlist" class="assignlist"></div>
  <div class="row">
    <button id="assign-save" type="button">归入</button>
    <button id="assign-cancel" class="secondary" type="button">取消</button>
  </div>
</div>
<div id="handpanel" class="handpanel" hidden>
  <h2>手写单一题目</h2>
  <label class="fld">题型
    <select id="hw-qtype">
      <option value="单选题">单选题</option>
      <option value="多选题">多选题</option>
      <option value="填空题">填空题</option>
      <option value="简答题">简答题</option>
      <option value="实验题">实验题</option>
      <option value="计算题">计算题</option>
    </select>
  </label>
  <label class="fld">大类
    <select id="hw-major"></select>
    <input id="hw-major-new" type="text" maxlength="50" placeholder="不填则为未分类"/>
  </label>
  <label class="fld">小类
    <select id="hw-minor"></select>
    <input id="hw-minor-new" type="text" maxlength="50" placeholder="不填则为未分类"/>
  </label>
  <label class="fld">文件来源
    <input id="hw-source" type="text" maxlength="500" placeholder="可留空" style="min-width:280px"/>
  </label>
  <label class="fld" style="align-items:flex-start">题干
    <textarea id="hw-body" placeholder="必填"></textarea>
  </label>
  <div class="row">
    <button id="hw-save" type="button">保存</button>
    <button id="hw-cancel" class="secondary" type="button">取消</button>
    <span id="hw-msg" class="msg"></span>
  </div>
</div>
<div class="stats" id="stats"></div>
<div class="papers">
  <h2>我的试卷</h2>
  <div id="paperlist" class="paperlist"></div>
</div>
<div id="paperview" class="paperview" hidden></div>
<main>
  <div class="pager">
    <button class="secondary" id="prev" type="button">上一页</button>
    <span id="pageinfo"></span>
    <button class="secondary" id="next" type="button">下一页</button>
    <label><input type="checkbox" id="pageall"/> 本页全选</label>
    <label><input type="checkbox" id="pagetext"/> 本页纯文字题</label>
    <button id="ai-stop-all" class="secondary" type="button">叫停所有当前AI生成</button>
    <button id="ai-compose" type="button" hidden>AI改写</button>
    <span id="ai-intensity-wrap" hidden>改动
      <select id="ai-intensity" title="变式改动程度">
        <option value="light">轻</option>
        <option value="medium" selected>中</option>
        <option value="deep">深</option>
      </select>
    </span>
    <span id="ai-msg"></span>
  </div>
  <div id="list"></div>
</main>
<script>
const major = document.getElementById('major');
const minor = document.getElementById('minor');
const q = document.getElementById('q');
const list = document.getElementById('list');
const picked = new Set();
let tree = {};
let offset = 0;
const limit = 20;
let total = 0;

function esc(s) {
  return (s || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function appendChemText(parent, part) {
  const target = part.vert === 'subscript' ? document.createElement('sub') : parent;
  if (target !== parent) parent.appendChild(target);
  (part.s || '').split(/(\^\{[^{}]+\}|_(?:[ \t\u00a0\u2000-\u200a\u202f\u3000]*_)*)/g).forEach(chunk => {
    if (chunk.startsWith('^{') && chunk.endsWith('}')) {
      const sup = document.createElement('sup');
      sup.textContent = chunk.slice(2, -1);
      target.appendChild(sup);
    } else if (chunk.startsWith('_')) {
      const blank = document.createElement('span');
      blank.className = 'answerblank';
      blank.setAttribute('role', 'img');
      blank.setAttribute('aria-label', '答题空');
      const length = [...chunk].reduce((n, c) => n + (c === '\u3000' ? 2 : 1), 0);
      blank.style.width = (length * 0.5) + 'em';
      target.appendChild(blank);
    } else target.appendChild(document.createTextNode(chunk));
  });
}
function appendQuestionParts(parent, parts, safeImages = false) {
  const merged = [];
  (parts || []).forEach(part => {
    if (!part) return;
    const last = merged[merged.length - 1];
    if (last && part.t === 'text' && last.t === 'text' && !part.omml && !last.omml && part.vert === last.vert) {
      last.s = (last.s || '') + (part.s || '');
    } else merged.push({...part});
  });
  merged.forEach(part => {
    if (part.t === 'text') appendChemText(parent, part);
    else if (part.t === 'img' && part.src && (!safeImages || aiSafeSrc(part.src))) {
      const img = document.createElement('img');
      img.src = part.src; img.alt = '题目图片';
      parent.appendChild(img);
    }
  });
}
let curriculum = null;
async function loadCurriculum() {
  const r = await fetch('/api/curriculum');
  curriculum = await r.json();
  const theme = document.getElementById('theme');
  curriculum.themes.forEach(name => theme.add(new Option(name, name)));
  function refresh() {
    const knowledge = document.getElementById('knowledge');
    knowledge.replaceChildren(new Option('全部知识点建议', ''));
    curriculum.points.filter(p => !theme.value || p.theme === theme.value).forEach(p => knowledge.add(new Option(p.name, p.id)));
  }
  theme.onchange = () => { refresh(); offset = 0; load(); };
  document.getElementById('knowledge').onchange = () => { offset = 0; load(); };
  refresh();
}
loadCurriculum().catch(() => {});
function paintPick(el, on) {
  if (!el) return;
  el.classList.toggle('pick-on', !!on);
  el.classList.toggle('pick-off', !on);
}
function paintAllPicks() {
  document.querySelectorAll('article[data-qid]').forEach(art => {
    paintPick(art, picked.has(Number(art.dataset.qid)));
  });
  document.querySelectorAll('.aiver').forEach(node => {
    const cb = node.querySelector('.ailine input[type="checkbox"]');
    paintPick(node, !!(cb && cb.checked));
  });
}
function updatePicked() {
  document.getElementById('picked').textContent = '已选 ' + picked.size + ' 题';
  paintAllPicks();
}
async function loadTree() {
  const keepM = major.value;
  const keepN = minor.value;
  const r = await fetch('/api/categories');
  const data = await r.json();
  tree = data.tree || {};
  major.innerHTML = '<option value="">全部大类</option>';
  Object.keys(tree).forEach(k => {
    const o = document.createElement('option');
    o.value = k; o.textContent = k;
    major.appendChild(o);
  });
  if ([...major.options].some(o => o.value === keepM)) major.value = keepM;
  fillMinor();
  if ([...minor.options].some(o => o.value === keepN)) minor.value = keepN;
}
function fillMinor() {
  const cur = minor.value;
  minor.innerHTML = '<option value="">全部小类</option>';
  const arr = major.value ? (tree[major.value] || []) : [];
  arr.forEach(k => {
    const o = document.createElement('option');
    o.value = k; o.textContent = k;
    minor.appendChild(o);
  });
  if ([...minor.options].some(o => o.value === cur)) minor.value = cur;
}
major.addEventListener('change', () => { minor.value = ''; fillMinor(); offset = 0; load(); });
minor.addEventListener('change', () => { offset = 0; load(); });
document.querySelectorAll('#types input').forEach(el => {
  el.addEventListener('change', () => { offset = 0; load(); });
});
document.getElementById('search').onclick = () => { offset = 0; load(); };
q.addEventListener('keydown', e => { if (e.key === 'Enter') { offset = 0; load(); } });

function fillQtype(bar, item) {
  bar.textContent = '';
  const lab = document.createElement('b');
  lab.textContent = '题型';
  const sel = document.createElement('select');
  sel.className = 'qtype';
  ['单选题','多选题','填空题','简答题','实验题','计算题'].forEach(name => {
    const o = document.createElement('option');
    o.value = name;
    o.textContent = name;
    if (name === item.qtype) o.selected = true;
    sel.appendChild(o);
  });
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.textContent = '保存题型';
  const msg = document.createElement('span');
  msg.className = 'msg';
  btn.addEventListener('click', async () => {
    btn.disabled = true;
    try {
      const r = await fetch('/api/questions/' + item.id + '/qtype', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({qtype: sel.value})
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) { alert(data.error || '保存失败'); msg.textContent = ''; return; }
      item.qtype = data.qtype;
      item.qtype_manual = 1;
      msg.textContent = '已保存';
    } finally {
      btn.disabled = false;
    }
  });
  bar.appendChild(lab);
  bar.appendChild(sel);
  bar.appendChild(btn);
  bar.appendChild(msg);
  if (item.image_count) {
    bar.appendChild(document.createTextNode('含图 ' + item.image_count));
  }
}
function uniqPush(arr, v) {
  if (v && arr.indexOf(v) < 0) arr.push(v);
}
function renderQuestion(item) {
  const art = document.createElement('article');
  art.dataset.qid = String(item.id);
  art.dataset.images = String(item.image_count || 0);
  art.dataset.origin = item.origin || 'human';
  paintPick(art, picked.has(item.id));
  const cb = document.createElement('input');
  cb.type = 'checkbox';
  cb.className = 'qpick';
  cb.checked = picked.has(item.id);
  cb.addEventListener('change', () => {
    if (cb.checked) picked.add(item.id); else picked.delete(item.id);
    paintPick(art, cb.checked);
    updatePicked();
  });
  const lab = document.createElement('div');
  lab.className = 'chk';
  const holder = document.createElement('div');
  holder.style.flex = '1';
  const cat = document.createElement('div');
  cat.className = 'qtypebar';
  fillQtype(cat, item);
  if (item.metadata) {
    const info = document.createElement('details');
    const summary = document.createElement('summary');
    const points = (item.metadata.knowledge || {}).points || [];
    const warnings = item.metadata.warnings || [];
    summary.textContent = warnings.length ? '待核对：' + warnings.join('；') : '查看识别依据与知识点建议';
    const details = document.createElement('p');
    details.textContent = '题型依据：' + ((item.metadata.type_suggestion || {}).reason || '教师指定')
      + '。知识点建议：' + (points.map(p => p.name).join('、') || '未识别') + '。标签供检索使用。';
    info.append(summary, details);
    cat.appendChild(info);
  }
  const edit = document.createElement('div');
  edit.className = 'catedit';
  edit.addEventListener('click', (e) => e.stopPropagation());
  const msg = document.createElement('span');
  msg.className = 'msg';
  const majorRow = document.createElement('div');
  majorRow.className = 'pickrow';
  const minorRow = document.createElement('div');
  minorRow.className = 'pickrow';
  function chosen(kind) {
    return [...edit.querySelectorAll('input[data-kind="' + kind + '"]:checked')].map(el => el.value);
  }
  function makeCheck(kind, value, on) {
    const label = document.createElement('label');
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.dataset.kind = kind;
    input.value = value;
    input.checked = !!on;
    if (kind === 'major') input.addEventListener('change', () => renderMinors(chosen('minor')));
    label.appendChild(input);
    label.appendChild(document.createTextNode(' ' + value));
    return label;
  }
  function renderMinors(prefer) {
    const majs = chosen('major');
    const mins = [];
    if (majs.length) majs.forEach(m => (tree[m] || []).forEach(n => uniqPush(mins, n)));
    (prefer || []).forEach(n => uniqPush(mins, n));
    minorRow.innerHTML = '';
    const b = document.createElement('b');
    b.textContent = '小类';
    minorRow.appendChild(b);
    mins.forEach(n => minorRow.appendChild(makeCheck('minor', n, (prefer || []).indexOf(n) >= 0)));
  }
  function renderMajors(preferMaj, preferMin) {
    const keys = Object.keys(tree);
    (preferMaj || []).forEach(m => uniqPush(keys, m));
    majorRow.innerHTML = '';
    const b = document.createElement('b');
    b.textContent = '大类';
    majorRow.appendChild(b);
    keys.forEach(k => majorRow.appendChild(makeCheck('major', k, (preferMaj || []).indexOf(k) >= 0)));
    renderMinors(preferMin || []);
  }
  const startMaj = (item.majors && item.majors.length) ? item.majors.slice() : (item.major ? [item.major] : []);
  const startMin = (item.minors && item.minors.length) ? item.minors.slice() : (item.minor ? [item.minor] : []);
  renderMajors(startMaj, startMin);
  const newRow = document.createElement('div');
  newRow.className = 'pickrow';
  const newLabel = document.createElement('label');
  newLabel.className = 'newpick';
  const newCb = document.createElement('input');
  newCb.type = 'checkbox';
  const newBox = document.createElement('span');
  newBox.className = 'newbox';
  const inMajor = document.createElement('input');
  inMajor.type = 'text';
  inMajor.maxLength = 50;
  inMajor.placeholder = '新大类';
  const inMinor = document.createElement('input');
  inMinor.type = 'text';
  inMinor.maxLength = 50;
  inMinor.placeholder = '新小类';
  newBox.appendChild(inMajor);
  newBox.appendChild(inMinor);
  newCb.addEventListener('change', () => {
    newBox.style.display = newCb.checked ? 'inline-flex' : 'none';
  });
  newLabel.appendChild(newCb);
  newLabel.appendChild(document.createTextNode(' 新建大/小类'));
  newRow.appendChild(newLabel);
  newRow.appendChild(newBox);
  const btnSave = document.createElement('button');
  btnSave.type = 'button';
  btnSave.textContent = '保存';
  async function postCat(body) {
    btnSave.disabled = true;
    try {
      const r = await fetch('/api/questions/' + item.id + '/category', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body)
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) { alert(data.error || '保存失败'); msg.textContent = ''; return false; }
      item.major = data.major;
      item.minor = data.minor;
      item.majors = data.majors || (data.major ? [data.major] : []);
      item.minors = data.minors || (data.minor ? [data.minor] : []);
      item.category_manual = 1;
      msg.textContent = '已保存';
      await loadTree();
      renderMajors(item.majors, item.minors);
      newCb.checked = false;
      newBox.style.display = 'none';
      inMajor.value = '';
      inMinor.value = '';
      return true;
    } finally {
      btnSave.disabled = false;
    }
  }
  btnSave.addEventListener('click', () => {
    const majors = chosen('major');
    const minors = chosen('minor');
    const body = {majors: majors, minors: minors};
    if (newCb.checked) {
      const nm = inMajor.value.trim();
      const nn = inMinor.value.trim();
      if (!nm && !nn) { alert('请填写新分类名称'); return; }
      if (nm) body.new_major = nm;
      if (nn) body.new_minor = nn;
    }
    if (!majors.length && !body.new_major) { alert('请选择大类'); return; }
    if (!minors.length && !body.new_minor) { alert('请选择小类'); return; }
    postCat(body);
  });
  edit.appendChild(majorRow);
  edit.appendChild(minorRow);
  edit.appendChild(newRow);
  edit.appendChild(btnSave);
  edit.appendChild(msg);
  const stem = document.createElement('div');
  stem.className = 'stem';
  function addParts(parent, parts) {
    appendQuestionParts(parent, parts);
  }
  (item.segments || []).forEach(para => {
    if (para && para.t === 'table') {
      const table = document.createElement('table');
      (para.rows || []).forEach(row => {
        const tr = document.createElement('tr');
        row.forEach(cell => {
          const td = document.createElement('td');
          addParts(td, cell);
          tr.appendChild(td);
        });
        table.appendChild(tr);
      });
      stem.appendChild(table);
      return;
    }
    const line = document.createElement('div');
    addParts(line, para);
    stem.appendChild(line);
  });
  if (!item.segments || !item.segments.length) {
    appendChemText(stem, {s: item.body || ''});
  }
  const src = document.createElement('div');
  src.className = 'src';
  const b = document.createElement('b');
  b.textContent = '文件来源：';
  src.appendChild(b);
  if (item.source_details && item.source_details.length) {
    item.source_details.forEach((source, i) => {
      if (i) src.appendChild(document.createTextNode('；'));
      const label = document.createElement('span');
      label.textContent = source.label;
      label.title = source.rel_path;
      src.appendChild(label);
    });
  } else src.appendChild(document.createTextNode((item.sources || []).join('；')));
  holder.appendChild(cat);
  const categoryPanel = document.createElement('details');
  const categorySummary = document.createElement('summary');
  categorySummary.textContent = '分类：' + startMin.join('、') + ' · 点击修改';
  categoryPanel.append(categorySummary, edit);
  holder.appendChild(categoryPanel);
  holder.appendChild(stem);
  holder.appendChild(src);
  if (item.answer) {
    const det = document.createElement('details');
    const sum = document.createElement('summary');
    sum.textContent = '答案 / 解析';
    det.appendChild(sum);
    const pre = document.createElement('div');
    pre.style.whiteSpace = 'pre-wrap';
    pre.textContent = item.answer;
    det.appendChild(pre);
    holder.appendChild(det);
  }
  const pencil = document.createElement('button');
  pencil.type = 'button';
  pencil.className = 'pencil';
  pencil.title = '编辑题干';
  pencil.setAttribute('aria-label', '编辑题干');
  pencil.textContent = '✎';
  pencil.addEventListener('click', (e) => {
    e.preventDefault();
    e.stopPropagation();
    const splitOpen = holder.querySelector('.splitedit');
    if (splitOpen) splitOpen.remove();
    const open = holder.querySelector('.stemedit');
    if (open) {
      const ta0 = open.querySelector('textarea');
      if (ta0) ta0.focus();
      return;
    }
    stem.style.display = 'none';
    const box = document.createElement('div');
    box.className = 'stemedit';
    const hint = document.createElement('div');
    hint.className = 'hint';
    hint.textContent = '文字可改。在光标处 Ctrl+V 粘贴截图，图片会立刻显示。点「删除此图」可去掉刚贴上的图，取消则不保存。';
    const blocks = [];
    let imgIndex = 0;
    function walkParts(parts) {
      let buf = '';
      function flush() {
        if (buf.length) { blocks.push({kind: 'text', text: buf}); buf = ''; }
      }
      (parts || []).forEach(part => {
        if (!part || typeof part !== 'object') return;
        if (part.t === 'img') {
          flush();
          blocks.push({kind: 'img', index: imgIndex, src: part.src || '', sha: part.sha || ''});
          imgIndex += 1;
        } else if (part.t === 'text') {
          buf += part.s || '';
        }
      });
      flush();
    }
    (item.segments || []).forEach(para => {
      if (para && para.t === 'table') {
        (para.rows || []).forEach(row => { (row || []).forEach(cell => walkParts(cell)); });
      } else if (Array.isArray(para)) {
        walkParts(para);
      }
    });
    if (!blocks.length) blocks.push({kind: 'text', text: item.body || ''});
    const list = document.createElement('div');
    list.className = 'blocks';
    function syncTexts() {
      const tas = list.querySelectorAll('textarea');
      const texts = blocks.filter(b => b.kind === 'text');
      if (tas.length !== texts.length) return;
      texts.forEach((b, i) => { b.text = tas[i].value; });
    }
    function renderBlocks() {
      syncTexts();
      list.textContent = '';
      blocks.forEach((b, i) => {
        const blk = document.createElement('div');
        blk.className = 'block';
        if (b.kind === 'img') {
          const img = document.createElement('img');
          img.src = b.src || b.dataUrl || '';
          img.alt = '题目图片';
          const tools = document.createElement('div');
          tools.className = 'imgtools';
          const del = document.createElement('button');
          del.type = 'button';
          del.className = 'secondary';
          del.textContent = '删除此图';
          del.addEventListener('click', () => {
            syncTexts();
            blocks.splice(i, 1);
            if (!blocks.length) blocks.push({kind: 'text', text: ''});
            renderBlocks();
          });
          tools.appendChild(del);
          blk.appendChild(img);
          blk.appendChild(tools);
        } else {
          const ta = document.createElement('textarea');
          ta.className = 'blocktext';
          ta.value = b.text || '';
          blk.appendChild(ta);
        }
        list.appendChild(blk);
      });
    }
    function insertImage(dataUrl) {
      syncTexts();
      const tas = [...list.querySelectorAll('textarea')];
      const active = document.activeElement;
      const ti = tas.indexOf(active);
      const img = {kind: 'img', dataUrl: dataUrl, src: dataUrl};
      if (ti >= 0) {
        let seen = 0;
        let bi = 0;
        for (; bi < blocks.length; bi++) {
          if (blocks[bi].kind !== 'text') continue;
          if (seen === ti) break;
          seen += 1;
        }
        const raw = active.value || '';
        const at = active.selectionStart || 0;
        const repl = [];
        if (raw.slice(0, at)) repl.push({kind: 'text', text: raw.slice(0, at)});
        repl.push(img);
        if (raw.slice(at)) repl.push({kind: 'text', text: raw.slice(at)});
        blocks.splice(bi, 1, ...repl);
      } else {
        blocks.push(img);
      }
      renderBlocks();
    }
    function takeImageFile(file) {
      if (!file) return;
      if (file.type !== 'image/png' && file.type !== 'image/jpeg') { alert('只能粘贴图片'); return; }
      if (file.size > 8 * 1024 * 1024) { alert('图片不能超过 8MB'); return; }
      const reader = new FileReader();
      reader.onload = () => { if (typeof reader.result === 'string') insertImage(reader.result); };
      reader.readAsDataURL(file);
    }
    box.addEventListener('paste', (ev) => {
      const items = (ev.clipboardData && ev.clipboardData.items) || [];
      let file = null;
      for (let i = 0; i < items.length; i++) {
        if (items[i].type === 'image/png' || items[i].type === 'image/jpeg') {
          file = items[i].getAsFile();
          break;
        }
      }
      if (!file) return;
      ev.preventDefault();
      takeImageFile(file);
    });
    box.addEventListener('dragover', (ev) => { ev.preventDefault(); });
    box.addEventListener('drop', (ev) => {
      ev.preventDefault();
      const files = (ev.dataTransfer && ev.dataTransfer.files) || [];
      if (files.length) takeImageFile(files[0]);
    });
    renderBlocks();
    const row = document.createElement('div');
    row.className = 'row';
    const save = document.createElement('button');
    save.type = 'button';
    save.textContent = '保存';
    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.className = 'secondary';
    cancel.textContent = '取消';
    const emsg = document.createElement('span');
    emsg.className = 'msg';
    cancel.addEventListener('click', () => {
      box.remove();
      stem.style.display = '';
    });
    save.addEventListener('click', async () => {
      syncTexts();
      const pieces = [];
      blocks.forEach(b => {
        if (b.kind === 'text') {
          if ((b.text || '').trim()) pieces.push({t: 'text', s: b.text});
        } else if (b.kind === 'img') {
          if (b.dataUrl) pieces.push({t: 'img', data: b.dataUrl});
          else pieces.push({t: 'img', i: b.index, sha: b.sha, src: b.src});
        }
      });
      if (!pieces.length) pieces.push({t: 'text', s: ''});
      save.disabled = true;
      try {
        const r = await fetch('/api/questions/' + item.id + '/body', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({pieces: pieces})
        });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) { alert(data.error || '保存失败'); emsg.textContent = ''; return; }
        item.body = data.body;
        if (data.segments) item.segments = data.segments;
        if (data.image_count != null) item.image_count = data.image_count;
        item.body_manual = 1;
        art.replaceWith(renderQuestion(item));
      } finally {
        save.disabled = false;
      }
    });
    row.appendChild(save);
    row.appendChild(cancel);
    row.appendChild(emsg);
    box.appendChild(hint);
    box.appendChild(list);
    box.appendChild(row);
    stem.insertAdjacentElement('afterend', box);
    const firstTa = list.querySelector('textarea');
    if (firstTa) firstTa.focus();
  });
  lab.appendChild(cb);
  lab.appendChild(holder);
  art.appendChild(lab);
  art.appendChild(pencil);
  const splitBtn = document.createElement('button');
  splitBtn.type = 'button';
  splitBtn.className = 'split';
  splitBtn.title = '分隔';
  splitBtn.setAttribute('aria-label', '分隔');
  splitBtn.textContent = '✂';
  splitBtn.addEventListener('click', (e) => {
    e.preventDefault();
    e.stopPropagation();
    const already = holder.querySelector('.splitedit');
    if (already) {
      const focusTa = already.querySelector('textarea');
      if (focusTa) focusTa.focus();
      return;
    }
    const stemOpen = holder.querySelector('.stemedit');
    if (stemOpen) stemOpen.remove();
    stem.style.display = 'none';
    const box = document.createElement('div');
    box.className = 'splitedit';
    function suggestTextSplit(text) {
      const lines = String(text || '').split('\n');
      const numRe = /^[0-9０-９]{1,3}\s*[.．、:：]/;
      const secRe = /^(第\s*[0-9一二三四五六七八九十百]+\s*单元|第\s*[IⅠⅡⅢIII]+\s*卷|[一二三四五六七八九十]+\s*、)/;
      function joinAt(i) {
        const head = lines.slice(0, i).join('\n').replace(/\s+$/, '');
        const tail = lines.slice(i).join('\n').replace(/^\s+/, '');
        if (head.trim() && tail.trim()) return [head, tail];
        return null;
      }
      for (let i = 1; i < lines.length - 1; i++) {
        if (!lines[i].trim()) {
          const hit = joinAt(i);
          if (hit) return hit;
        }
      }
      for (let i = 1; i < lines.length; i++) {
        const t = lines[i].trim();
        if (numRe.test(t) || secRe.test(t)) {
          const hit = joinAt(i);
          if (hit) return hit;
        }
      }
      return null;
    }
    function blocksFromItem(it) {
      const blocks = [];
      let imgIndex = 0;
      function walkParts(parts) {
        let buf = '';
        function flush() {
          if (buf.length) {
            blocks.push({kind: 'text', text: buf});
            buf = '';
          }
        }
        (parts || []).forEach(part => {
          if (!part || typeof part !== 'object') return;
          if (part.t === 'img') {
            flush();
            blocks.push({kind: 'img', index: imgIndex, src: part.src || '', sha: part.sha || ''});
            imgIndex += 1;
          } else if (part.t === 'text') {
            buf += part.s || '';
          }
        });
        flush();
      }
      (it.segments || []).forEach(para => {
        if (para && para.t === 'table') {
          (para.rows || []).forEach(row => {
            (row || []).forEach(cell => walkParts(cell));
          });
        } else if (Array.isArray(para)) {
          walkParts(para);
        }
      });
      if (!blocks.length) blocks.push({kind: 'text', text: it.body || ''});
      return blocks;
    }
    const hint = document.createElement('div');
    hint.className = 'hint';
    hint.textContent = '图片按原顺序显示，整张分到切开的一边，不会被丢掉。在两块之间点「从这里切开」。一块文字里如果还粘着两题，把光标放好再点「在此分割」。';
    const blocks = blocksFromItem(item);
    const numReStart = /^[0-9０-９]{1,3}\s*[.．、:：]/;
    const secReStart = /^(第\s*[0-9一二三四五六七八九十百]+\s*单元|第\s*[IⅠⅡⅢIII]+\s*卷|[一二三四五六七八九十]+\s*、)/;
    let cutAt = blocks.length;
    for (let i = 1; i < blocks.length; i++) {
      if (blocks[i].kind === 'text') {
        const t = (blocks[i].text || '').trim();
        if (numReStart.test(t) || secReStart.test(t)) { cutAt = i; break; }
      }
    }
    if (cutAt === blocks.length) {
      for (let i = 0; i < blocks.length; i++) {
        if (blocks[i].kind !== 'text') continue;
        const hit = suggestTextSplit(blocks[i].text || '');
        if (hit) {
          blocks.splice(i, 1, {kind: 'text', text: hit[0]}, {kind: 'text', text: hit[1]});
          cutAt = i + 1;
          break;
        }
      }
    }
    if (cutAt === blocks.length && blocks.length >= 2) cutAt = 1;
    const list = document.createElement('div');
    list.className = 'blocks';
    function syncTexts() {
      const tas = list.querySelectorAll('textarea');
      const texts = blocks.filter(b => b.kind === 'text');
      if (tas.length !== texts.length) return;
      texts.forEach((b, i) => { b.text = tas[i].value; });
    }
    function renderBlocks() {
      syncTexts();
      list.textContent = '';
      blocks.forEach((b, i) => {
        if (i > 0) {
          const line = document.createElement('label');
          line.className = 'cutline';
          const radio = document.createElement('input');
          radio.type = 'radio';
          radio.name = 'splitcut' + item.id;
          radio.checked = cutAt === i;
          radio.addEventListener('change', () => { cutAt = i; renderBlocks(); });
          line.appendChild(radio);
          line.appendChild(document.createTextNode(' 从这里切开'));
          list.appendChild(line);
        }
        const blk = document.createElement('div');
        blk.className = 'block ' + (i < cutAt ? 'headside' : 'tailside');
        if (i === 0 || i === cutAt) {
          const lab = document.createElement('div');
          lab.className = 'lbl';
          lab.textContent = i < cutAt ? '上半' : '下半';
          blk.appendChild(lab);
        }
        if (b.kind === 'img') {
          const img = document.createElement('img');
          img.src = b.src;
          img.alt = '题目图片';
          blk.appendChild(img);
        } else {
          const ta = document.createElement('textarea');
          ta.className = 'blocktext';
          ta.value = b.text || '';
          const cutBtn = document.createElement('button');
          cutBtn.type = 'button';
          cutBtn.className = 'secondary';
          cutBtn.textContent = '在此分割';
          cutBtn.addEventListener('click', () => {
            syncTexts();
            const at = ta.selectionStart || 0;
            const raw = ta.value;
            blocks.splice(i, 1, {kind: 'text', text: raw.slice(0, at)}, {kind: 'text', text: raw.slice(at)});
            cutAt = i + 1;
            renderBlocks();
          });
          blk.appendChild(ta);
          blk.appendChild(cutBtn);
        }
        list.appendChild(blk);
      });
    }
    renderBlocks();
    const row = document.createElement('div');
    row.className = 'row';
    const save = document.createElement('button');
    save.type = 'button';
    save.textContent = '保存';
    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.className = 'secondary';
    cancel.textContent = '取消';
    cancel.addEventListener('click', () => {
      box.remove();
      stem.style.display = '';
    });
    save.addEventListener('click', async () => {
      syncTexts();
      function piecesOf(arr) {
        const out = [];
        arr.forEach(b => {
          if (b.kind === 'text') {
            if ((b.text || '').trim()) out.push({t: 'text', s: b.text});
          } else if (b.kind === 'img') {
            out.push({t: 'img', i: b.index, src: b.src, sha: b.sha});
          }
        });
        return out;
      }
      function hasStuff(ps) {
        return ps.some(p => (p.t === 'text' && String(p.s || '').trim()) || p.t === 'img');
      }
      const head = piecesOf(blocks.slice(0, cutAt));
      const tail = piecesOf(blocks.slice(cutAt));
      if (!hasStuff(head) || !hasStuff(tail)) {
        alert('上下两部都要有内容');
        return;
      }
      save.disabled = true;
      try {
        const payload = {head: head, tail: tail};
        if (currentPaperId) payload.paper_id = currentPaperId;
        const r = await fetch('/api/questions/' + item.id + '/split', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload)
        });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) { alert(data.error || '保存失败'); return; }
        item.body = data.body;
        if (data.segments) item.segments = data.segments;
        if (data.image_count != null) item.image_count = data.image_count;
        item.body_manual = 1;
        if (data.paper && currentPaperId && data.paper.id === currentPaperId) {
          openPaperIds = (data.paper.ids || []).slice();
          if (data.new_id) picked.add(data.new_id);
          updatePicked();
          if (paperFilter && paperFilter.id === data.paper.id) {
            paperFilter.name = data.paper.name || paperFilter.name;
            paperFilter.ids = (data.paper.ids || []).slice();
            setPaperViewLine();
          }
          loadPapers();
        }
        const fresh = renderQuestion(item);
        art.replaceWith(fresh);
        if (data.question) fresh.insertAdjacentElement('afterend', renderQuestion(data.question));
        alert('已分成两道题');
      } finally {
        save.disabled = false;
      }
    });
    row.appendChild(save);
    row.appendChild(cancel);
    box.appendChild(hint);
    box.appendChild(list);
    box.appendChild(row);
  });
  art.appendChild(splitBtn);
  const trash = document.createElement('button');
  trash.type = 'button';
  trash.className = 'trash';
  trash.title = '删除这道题';
  trash.setAttribute('aria-label', '删除这道题');
  trash.textContent = '✕';
  trash.addEventListener('click', async (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (!window.confirm('确定删除这道题吗？')) return;
    trash.disabled = true;
    try {
      const r = await fetch('/api/questions/' + item.id, {method: 'DELETE'});
      const data = await r.json().catch(() => ({}));
      if (!r.ok) { alert(data.error || '删除失败'); return; }
      picked.delete(item.id);
      updatePicked();
      openPaperIds = openPaperIds.filter(id => id !== item.id);
      if (paperFilter && paperFilter.ids) {
        paperFilter.ids = paperFilter.ids.filter(id => id !== item.id);
        setPaperViewLine();
      }
      art.remove();
      loadPapers();
      load();
    } finally {
      trash.disabled = false;
    }
  });
  art.appendChild(trash);
  if (paperFilter && paperFilter.ids && paperFilter.ids.indexOf(item.id) >= 0) {
    const idx = paperFilter.ids.indexOf(item.id);
    const movers = document.createElement('div');
    movers.className = 'movers';
    function addMove(dir, label, glyph) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'move';
      b.title = label;
      b.setAttribute('aria-label', label);
      b.textContent = glyph;
      b.addEventListener('click', async (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        if (!paperFilter || !paperFilter.id) return;
        b.disabled = true;
        const qid = item.id;
        const beforeTop = art.getBoundingClientRect().top;
        try {
          const r = await fetch('/api/papers/' + paperFilter.id + '/move', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({question_id: item.id, dir: dir})
          });
          const data = await r.json().catch(() => ({}));
          if (!r.ok) { alert(data.error || '移动失败'); return; }
          if (data.ids && paperFilter && paperFilter.id === data.id) {
            paperFilter.ids = data.ids.slice();
            if (currentPaperId === data.id) openPaperIds = data.ids.slice();
            const keep = new Set(picked);
            picked.clear();
            data.ids.forEach(id => { if (keep.has(id)) picked.add(id); });
            updatePicked();
            setPaperViewLine();
          }
          await load();
          const next = list.querySelector('article[data-qid="' + qid + '"]');
          if (next) {
            const afterTop = next.getBoundingClientRect().top;
            window.scrollBy(0, afterTop - beforeTop);
          }
        } finally {
          b.disabled = false;
        }
      });
      movers.appendChild(b);
    }
    if (idx > 0) addMove('up', '上移', '↑');
    if (idx < paperFilter.ids.length - 1) addMove('down', '下移', '↓');
    if (movers.childNodes.length) {
      art.classList.add('has-movers');
      art.appendChild(movers);
    }
  }
  return art;
}

async function load() {
  const u = new URL('/api/questions', location.origin);
  if (paperFilter) {
    offset = 0;
    u.searchParams.set('ids', (paperFilter.ids || []).join(','));
    u.searchParams.set('offset', '0');
    u.searchParams.set('limit', String(Math.max(1, (paperFilter.ids || []).length)));
  } else {
    u.searchParams.set('q', q.value.trim());
    u.searchParams.set('major', major.value);
    u.searchParams.set('minor', minor.value);
    u.searchParams.set('theme', document.getElementById('theme').value);
    u.searchParams.set('knowledge', document.getElementById('knowledge').value);
    const types = [...document.querySelectorAll('#types input:checked')].map(el => el.value);
    if (types.length) u.searchParams.set('types', types.join(','));
    u.searchParams.set('offset', offset);
    u.searchParams.set('limit', limit);
  }
  const r = await fetch(u);
  const data = await r.json();
  total = data.total || 0;
  list.innerHTML = '';
  if (!data.items || !data.items.length) {
    list.innerHTML = paperFilter
      ? '<div class="empty">这套试卷没有题目</div>'
      : '<div class="empty">没有符合条件的题目</div>';
  } else {
    data.items.forEach(it => {
      if (paperFilter && it.origin === 'ai' && it.base_question_id) {
        const ids = paperFilter.ids || [];
        if (ids.indexOf(it.base_question_id) >= 0) return;
      }
      list.appendChild(renderQuestion(it));
    });
    if (window.chemBankMountAi) window.chemBankMountAi();
  }
  paintAllPicks();
  const page = Math.floor(offset / limit) + 1;
  const pages = Math.max(1, Math.ceil(total / limit));
  document.getElementById('pageinfo').textContent = '第 ' + page + ' / ' + pages + ' 页，共 ' + total + ' 题';
  document.getElementById('stats').textContent = data.stats_line || '';
  document.getElementById('pageall').checked = false;
  const pagetext = document.getElementById('pagetext');
  if (pagetext) pagetext.checked = false;
}
document.getElementById('prev').onclick = () => { offset = Math.max(0, offset - limit); load(); };
document.getElementById('next').onclick = () => { if (offset + limit < total) { offset += limit; load(); } };
document.getElementById('pageall').onchange = (e) => {
  list.querySelectorAll('input.qpick').forEach(cb => {
    cb.checked = e.target.checked;
    cb.dispatchEvent(new Event('change'));
  });
};
const textOnlyPicked = new Set();
document.getElementById('pagetext').onchange = (e) => {
  list.querySelectorAll('article').forEach(art => {
    const cb = art.querySelector('input.qpick');
    if (!cb) return;
    const id = Number(art.dataset.qid);
    const textOnly = Number(art.dataset.images || 0) === 0;
    if (e.target.checked) {
      const want = textOnly;
      if (cb.checked !== want) {
        cb.checked = want;
        cb.dispatchEvent(new Event('change'));
      }
      if (want) textOnlyPicked.add(id);
      else textOnlyPicked.delete(id);
    } else if (textOnlyPicked.has(id)) {
      textOnlyPicked.delete(id);
      if (cb.checked) {
        cb.checked = false;
        cb.dispatchEvent(new Event('change'));
      }
    }
  });
};
document.getElementById('export').onclick = async () => {
  if (!picked.size) { alert('请先勾选题目'); return; }
  const r = await fetch('/api/export', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ids: [...picked], keep_source: document.getElementById('keepsrc').checked, auto_number: document.getElementById('autonum').checked, keep_answers: document.getElementById('keepanswers').checked})
  });
  if (!r.ok) { const error = await r.json().catch(() => ({})); alert(error.error || '导出失败'); return; }
  const blob = await r.blob();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = '化学试卷.docx';
  a.click();
};
document.getElementById('file').onchange = async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  const fd = new FormData();
  fd.append('file', f);
  if (currentPaperId) fd.append('paper_id', String(currentPaperId));
  const r = await fetch('/api/import', {method: 'POST', body: fd});
  const data = await r.json().catch(() => ({}));
  if (!r.ok) { alert(data.error || '导入失败'); e.target.value = ''; return; }
  let msg = '已导入，新题 ' + (data.new || 0) + '，合并重复 ' + (data.merged || 0);
  if (data.review_count) msg += '\n其中 ' + data.review_count + ' 题需要核对。每道题上方可查看识别提醒。';
  if (data.paper) {
    msg += '，已加入正在编辑的试卷 ' + (data.added_to_paper || 0) + ' 题（卷上已有的不重复添加）';
    (data.question_ids || []).forEach(qid => picked.add(qid));
    updatePicked();
    if (data.paper.id === currentPaperId && data.paper.ids) openPaperIds = data.paper.ids.slice();
    if (paperFilter && paperFilter.id === data.paper.id) {
      paperFilter.name = data.paper.name || paperFilter.name;
      if (data.paper.ids) paperFilter.ids = data.paper.ids.slice();
      setPaperViewLine();
    }
    loadPapers();
  }
  alert(msg);
  offset = 0;
  loadTree().then(load);
  e.target.value = '';
};
let currentPaperId = null;
let paperFilter = null;
let paperClickTimer = null;
let openPaperIds = [];
let paperReady = false;

function syncAiCompose() {
  const btn = document.getElementById('ai-compose');
  if (!btn) return;
  const on = !!(currentPaperId || (paperFilter && paperFilter.id));
  btn.hidden = !on;
  const wrap = document.getElementById('ai-intensity-wrap');
  if (wrap) wrap.hidden = !on;
}

function rememberPaper() {
  if (!paperReady) return;
  const u = new URL(location.href);
  const id = (paperFilter && paperFilter.id) || currentPaperId;
  if (!id) {
    u.searchParams.delete('paper');
    u.searchParams.delete('view');
  } else {
    u.searchParams.set('paper', String(id));
    if (paperFilter && paperFilter.id === id) u.searchParams.set('view', '1');
    else u.searchParams.delete('view');
  }
  const next = u.pathname + u.search + u.hash;
  if (next !== location.pathname + location.search + location.hash) {
    history.replaceState(null, '', next);
  }
}

function setPaperViewLine() {
  const el = document.getElementById('paperview');
  el.textContent = '';
  syncAiCompose();
  if (!paperFilter) {
    el.hidden = true;
    rememberPaper();
    return;
  }
  el.hidden = false;
  const name = paperFilter.name || '未命名试卷';
  const span = document.createElement('span');
  span.textContent = '正在查看：' + name + '，只显示这套的题目';
  const back = document.createElement('button');
  back.type = 'button';
  back.className = 'secondary';
  back.textContent = '返回全部';
  back.addEventListener('click', () => {
    paperFilter = null;
    offset = 0;
    setPaperViewLine();
    load();
  });
  el.appendChild(span);
  el.appendChild(back);
  rememberPaper();
}

function setPaperHint() {
  const el = document.getElementById('paperhint');
  if (currentPaperId) {
    const name = document.getElementById('papername').value.trim() || '未命名试卷';
    el.textContent = '正在编辑：' + name + '（再点从0组卷会保存到这一份）';
  } else {
    el.textContent = '未打开试卷，点从0组卷会新建一份';
  }
  syncAiCompose();
}

async function loadPapers() {
  const r = await fetch('/api/papers');
  const data = await r.json().catch(() => ({}));
  const box = document.getElementById('paperlist');
  box.textContent = '';
  const papers = data.papers || [];
  if (!papers.length) {
    const empty = document.createElement('span');
    empty.className = 'empty';
    empty.textContent = '还没有保存的试卷';
    box.appendChild(empty);
    return;
  }
  papers.forEach(p => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'secondary paper' + (p.id === currentPaperId ? ' on' : '');
    b.textContent = (p.name || '未命名试卷') + '（' + (p.count || 0) + '题）';
    b.title = '单击打开继续编辑；双击只看这套题目';
    b.addEventListener('click', () => {
      if (paperClickTimer) {
        clearTimeout(paperClickTimer);
        paperClickTimer = null;
        return;
      }
      paperClickTimer = setTimeout(() => {
        paperClickTimer = null;
        paperFilter = null;
        setPaperViewLine();
        openPaper(p.id);
      }, 280);
    });
    b.addEventListener('dblclick', (ev) => {
      ev.preventDefault();
      if (paperClickTimer) {
        clearTimeout(paperClickTimer);
        paperClickTimer = null;
      }
      openPaper(p.id, {view: true});
    });
    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'secondary';
    del.textContent = '删除';
    del.addEventListener('click', async () => {
      if (!confirm('删除试卷「' + (p.name || '') + '」？题目本身不会删除。')) return;
      const rr = await fetch('/api/papers/' + p.id, {method: 'DELETE'});
      const body = await rr.json().catch(() => ({}));
      if (!rr.ok) { alert(body.error || '删除失败'); return; }
      if (currentPaperId === p.id) {
        currentPaperId = null;
        openPaperIds = [];
        document.getElementById('papername').value = '';
        setPaperHint();
      }
      if (paperFilter && paperFilter.id === p.id) {
        paperFilter = null;
        setPaperViewLine();
        offset = 0;
        load();
      }
      loadPapers();
    });
    box.appendChild(b);
    box.appendChild(del);
  });
}

async function openPaper(id, opts) {
  const r = await fetch('/api/papers/' + id);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) { alert(data.error || '打开失败'); return; }
  currentPaperId = data.id;
  openPaperIds = (data.ids || []).slice();
  document.getElementById('papername').value = data.name || '';
  picked.clear();
  (data.ids || []).forEach(qid => picked.add(qid));
  updatePicked();
  if (opts && opts.view) {
    paperFilter = {
      id: data.id,
      name: data.name || '未命名试卷',
      ids: (data.ids || []).slice()
    };
    offset = 0;
  }
  setPaperHint();
  setPaperViewLine();
  await load();
  loadPapers();
}

document.getElementById('assignpapers').onclick = async () => {
  if (!picked.size) { alert('请至少选择一道题目'); return; }
  const r = await fetch('/api/papers');
  const data = await r.json().catch(() => ({}));
  const list = document.getElementById('assignlist');
  list.textContent = '';
  (data.papers || []).forEach(p => {
    const lab = document.createElement('label');
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.value = String(p.id);
    lab.appendChild(cb);
    lab.appendChild(document.createTextNode(' ' + (p.name || '未命名试卷')));
    list.appendChild(lab);
  });
  document.getElementById('assignpanel').hidden = false;
};
document.getElementById('assign-cancel').onclick = () => {
  document.getElementById('assignpanel').hidden = true;
};
document.getElementById('assign-save').onclick = async () => {
  if (!picked.size) { alert('请至少选择一道题目'); return; }
  const paperIds = [...document.querySelectorAll('#assignlist input:checked')].map(el => Number(el.value));
  if (!paperIds.length) { alert('请至少选择一套试卷'); return; }
  const btn = document.getElementById('assign-save');
  btn.disabled = true;
  try {
    const r = await fetch('/api/papers/assign', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({question_ids: [...picked], paper_ids: paperIds})
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) { alert(data.error || '保存失败'); return; }
    (data.papers || []).forEach(p => {
      if (currentPaperId === p.id && p.ids) openPaperIds = p.ids.slice();
      if (paperFilter && paperFilter.id === p.id && p.ids) {
        paperFilter.ids = p.ids.slice();
        setPaperViewLine();
        load();
      }
    });
    document.getElementById('assignpanel').hidden = true;
    alert('已归入 ' + (data.paper_count || paperIds.length) + ' 套试卷');
    loadPapers();
  } finally {
    btn.disabled = false;
  }
};

document.getElementById('makepaper').onclick = async () => {
  if (!picked.size) { alert('请至少选择一道题目'); return; }
  const body = {name: document.getElementById('papername').value, ids: [...picked]};
  const url = currentPaperId ? ('/api/papers/' + currentPaperId) : '/api/papers';
  const r = await fetch(url, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) { alert(data.error || '保存失败'); return; }
  currentPaperId = data.id;
  document.getElementById('papername').value = data.name || '';
  if (data.ids) {
    openPaperIds = data.ids.slice();
    picked.clear();
    data.ids.forEach(qid => picked.add(qid));
    updatePicked();
  }
  if (paperFilter && paperFilter.id === data.id) {
    paperFilter.name = data.name || paperFilter.name;
    if (data.ids) paperFilter.ids = data.ids.slice();
    setPaperViewLine();
  }
  setPaperHint();
  load();
  loadPapers();
};

document.getElementById('newpaper').onclick = () => {
  currentPaperId = null;
  openPaperIds = [];
  document.getElementById('papername').value = '';
  const wasView = !!paperFilter;
  paperFilter = null;
  setPaperHint();
  setPaperViewLine();
  loadPapers();
  if (wasView) { offset = 0; load(); }
};

document.getElementById('exportpaper').onclick = async () => {
  if (!currentPaperId) { alert('请先打开一套试卷'); return; }
  const r0 = await fetch('/api/papers/' + currentPaperId);
  const data = await r0.json().catch(() => ({}));
  if (!r0.ok) { alert(data.error || '导出失败'); return; }
  const ids = data.ids || [];
  if (!ids.length) { alert('请至少选择一道题目'); return; }
  const r = await fetch('/api/export', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ids: ids, keep_source: document.getElementById('keepsrc').checked, auto_number: document.getElementById('autonum').checked, keep_answers: document.getElementById('keepanswers').checked})
  });
  if (!r.ok) { const error = await r.json().catch(() => ({})); alert(error.error || '导出失败'); return; }
  const blob = await r.blob();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = (data.name || '化学试卷') + '.docx';
  a.click();
};

// AI 组卷 hook: window.chemBankAiCompose({paperId, paperName, questionIds}). GET /api/papers/<id>/ai-compose. Do not implement the model here.
document.getElementById('ai-compose').onclick = () => {
  const paperId = currentPaperId || (paperFilter && paperFilter.id) || null;
  if (!paperId) return;
  const paperName = (document.getElementById('papername').value || '').trim() || (paperFilter && paperFilter.name) || '未命名试卷';
  const onPaper = new Set((paperFilter && paperFilter.id === paperId && paperFilter.ids) ? paperFilter.ids : openPaperIds);
  const questionIds = [...picked].filter(id => onPaper.has(id));
  if (!questionIds.length) { alert('请至少选择一道题目'); return; }
  const detail = {paperId: paperId, paperName: paperName, questionIds: questionIds};
  if (typeof window.chemBankAiCompose === 'function') window.chemBankAiCompose(detail);
};

function fillHandMinors() {
  const selN = document.getElementById('hw-minor');
  const keep = selN.value;
  selN.innerHTML = '<option value="">请选择</option>';
  const m = document.getElementById('hw-major').value;
  const typed = document.getElementById('hw-major-new').value.trim();
  const arr = [];
  if (m && !typed) (tree[m] || []).forEach(n => uniqPush(arr, n));
  else Object.keys(tree).forEach(k => (tree[k] || []).forEach(n => uniqPush(arr, n)));
  arr.forEach(k => {
    const o = document.createElement('option');
    o.value = k;
    o.textContent = k;
    selN.appendChild(o);
  });
  if ([...selN.options].some(o => o.value === keep)) selN.value = keep;
}
function fillHandMajors() {
  const selM = document.getElementById('hw-major');
  const keep = selM.value;
  selM.innerHTML = '<option value="">请选择</option>';
  Object.keys(tree).forEach(k => {
    const o = document.createElement('option');
    o.value = k;
    o.textContent = k;
    selM.appendChild(o);
  });
  if ([...selM.options].some(o => o.value === keep)) selM.value = keep;
  fillHandMinors();
}
document.getElementById('hw-major').addEventListener('change', fillHandMinors);
document.getElementById('hw-major-new').addEventListener('input', fillHandMinors);
document.getElementById('handwrite').onclick = async () => {
  const panel = document.getElementById('handpanel');
  await loadTree();
  fillHandMajors();
  document.getElementById('hw-msg').textContent = '';
  panel.hidden = false;
  document.getElementById('hw-body').focus();
};
document.getElementById('hw-cancel').onclick = () => {
  document.getElementById('handpanel').hidden = true;
  document.getElementById('hw-msg').textContent = '';
};
document.getElementById('hw-save').onclick = async () => {
  const qtype = document.getElementById('hw-qtype').value;
  const major = document.getElementById('hw-major').value;
  const newMajor = document.getElementById('hw-major-new').value.trim();
  const minor = document.getElementById('hw-minor').value;
  const newMinor = document.getElementById('hw-minor-new').value.trim();
  const source = document.getElementById('hw-source').value;
  const body = document.getElementById('hw-body').value;
  if (!qtype) { alert('请选择题型'); return; }
  if (!body.trim()) { alert('请填写题干'); return; }
  if (!newMajor && !major) major = '未分类';
  if (!newMinor && !minor) minor = '未分类';
  const payload = {qtype: qtype, major: major, minor: minor, source: source, body: body};
  if (newMajor) payload.new_major = newMajor;
  if (newMinor) payload.new_minor = newMinor;
  if (currentPaperId) payload.paper_id = currentPaperId;
  const btn = document.getElementById('hw-save');
  btn.disabled = true;
  try {
    const r = await fetch('/api/questions', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) { alert(data.error || '保存失败'); return; }
    if (data.paper && currentPaperId && data.paper.id === currentPaperId) {
      openPaperIds = (data.paper.ids || []).slice();
      if (data.id) picked.add(data.id);
      updatePicked();
      if (paperFilter && paperFilter.id === data.paper.id) {
        paperFilter.name = data.paper.name || paperFilter.name;
        paperFilter.ids = (data.paper.ids || []).slice();
        setPaperViewLine();
      }
    }
    document.getElementById('hw-body').value = '';
    document.getElementById('hw-source').value = '';
    document.getElementById('hw-major-new').value = '';
    document.getElementById('hw-minor-new').value = '';
    document.getElementById('hw-msg').textContent = '已保存';
    let msg = '已保存这道题';
    if (data.added_to_paper) msg += '，并加入正在编辑的试卷';
    alert(msg);
    document.getElementById('handpanel').hidden = true;
    await loadTree();
    offset = 0;
    await load();
    loadPapers();
  } finally {
    btn.disabled = false;
  }
};

document.getElementById('papername').addEventListener('input', setPaperHint);
setPaperHint();
setPaperViewLine();
loadPapers();
let aiPollTimer = null;
let aiPollPaper = null;

const AI_STOP_NOTICE = '该题多次尝试但结果存疑，请自行更改';
const AI_RETRY_NOTICE = '新题检查未通过，正在重新改写并检查，请稍等。';
function aiProgressText(job) {
  if (!job) return null;
  if (job.status === 'queued') return {
    title: '正在等待开始',
    detail: '这道题已经提交，轮到它时会自动开始处理。'
  };
  return ({
    profile: {title: '正在读懂原题', detail: 'AI 正在看题干和图片，确认这道题考什么、哪些内容需要保留。'},
    generate: {title: '正在改写题目', detail: 'AI 正在按你的要求写新题，并整理答案和解析。'},
    judge: {title: '正在检查题目和答案', detail: '新题已经写好，AI 正在再检查一遍，确认题目、图片和答案是否对应。'}
  })[job.phase] || {title: '正在处理这道题', detail: '处理完成后，结果会自动显示在这里。'};
}
function aiFriendlyError(message) {
  const text = String(message || '');
  if (/32 张|超过.*图片上限/.test(text)) return '这道题的图片太多，请先检查是否把几道题连在了一起，并拆开后重试。';
  if (/图片|image/i.test(text)) return '这道题的图片缺失或无法读取，暂时不能改写。请检查图片，必要时从原 Word 文件重新导入这道题。';
  if (/输出额度|output_limit|finish_reason.*length/.test(text)) return 'AI 这次没有写完完整题目，请稍后重新生成。';
  if (/429|并发|限流|1302/.test(text)) return 'AI 服务现在比较忙，这次没有完成，请稍后重试。';
  if (/超时|限时|心跳|timeout/i.test(text)) return '这次等待太久，仍没有拿到完整结果，请稍后重试。';
  if (/密钥|权限|401|403|auth/i.test(text)) return '暂时无法使用 AI 服务，请联系配置这个程序的人检查设置。';
  if (/配置|config/i.test(text)) return 'AI 服务还没有设置好，请联系配置这个程序的人检查设置。';
  if (/JSON|正文|parse|empty/i.test(text)) return 'AI 这次返回的内容不完整，暂时不能作为新题使用，请重新生成。';
  return '这次没有完成处理，请稍后重试；如果仍然失败，请联系配置这个程序的人帮忙查看。';
}
function aiStatusText(st) {
  if (st === 'QUEUED' || st === 'GENERATING') return '生成中';
  if (st === 'GENERATED' || st === 'JUDGING') return '审核中';
  if (st === 'PASS') return '已校验';
  if (st === 'SUSPECT') return '存疑，请检查';
  if (st === 'FAIL') return '审核未通过';
  if (st === 'GENERATION_ERROR') return '生成没有完成';
  if (st === 'JUDGE_ERROR') return '审核没有完成';
  if (st === 'CANCELLED') return '已叫停';
  return '';
}
function aiStatusClass(st) {
  if (st === 'PASS') return 'pass';
  if (st === 'SUSPECT') return 'suspect';
  if (st === 'FAIL' || st === 'GENERATION_ERROR' || st === 'JUDGE_ERROR') return 'fail';
  return 'run';
}
function aiLevelText(v) {
  if (v === 'light') return '轻';
  if (v === 'deep') return '深';
  return '中';
}
function aiSafeSrc(src) {
  return /^\/media\/[0-9a-f]{64}\.(png|jpg|gif|bmp|webp)$/.test(src || '');
}
function aiFillStem(stem, segments, body) {
  function addParts(parent, parts) {
    appendQuestionParts(parent, parts, true);
  }
  (segments || []).forEach(para => {
    if (para && para.t === 'table') {
      const table = document.createElement('table');
      (para.rows || []).forEach(row => {
        const tr = document.createElement('tr');
        row.forEach(cell => {
          const td = document.createElement('td');
          addParts(td, cell);
          tr.appendChild(td);
        });
        table.appendChild(tr);
      });
      stem.appendChild(table);
      return;
    }
    const line = document.createElement('div');
    addParts(line, para);
    stem.appendChild(line);
  });
  if (!segments || !segments.length) appendChemText(stem, {s: body || ''});
}
function aiIntensitySelect(value) {
  const sel = document.createElement('select');
  [['light','轻'],['medium','中'],['deep','深']].forEach(pair => {
    const o = document.createElement('option');
    o.value = pair[0];
    o.textContent = pair[1];
    if (pair[0] === (value || 'medium')) o.selected = true;
    sel.appendChild(o);
  });
  return sel;
}
function aiPaperId() {
  return (paperFilter && paperFilter.id) || currentPaperId || null;
}
function aiApplyPaper(paper) {
  if (!paper || !paper.ids) return;
  openPaperIds = paper.ids.slice();
  if (currentPaperId === paper.id) {
    picked.clear();
    paper.ids.forEach(id => picked.add(id));
    updatePicked();
    list.querySelectorAll('article').forEach(art => {
      const cb = art.querySelector('input.qpick');
      if (!cb) return;
      cb.checked = picked.has(Number(art.dataset.qid));
    });
  }
  if (paperFilter && paperFilter.id === paper.id) {
    paperFilter.ids = paper.ids.slice();
    if (paper.name) paperFilter.name = paper.name;
    const el = document.getElementById('paperview');
    const span = el.querySelector('span');
    if (span) span.textContent = '正在查看：' + (paperFilter.name || '未命名试卷') + '，只显示这套的题目';
  }
  loadPapers();
}
async function aiSetOnPaper(baseId, questionId, on) {
  const paperId = aiPaperId();
  if (!paperId) { alert('请先打开一套试卷'); return false; }
  if (on) {
    const r = await fetch('/api/papers/' + paperId + '/items', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({question_id: questionId, after_question_id: baseId})
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) { alert(data.error || '放入试卷失败'); return false; }
    aiApplyPaper(data);
    return true;
  }
  const r0 = await fetch('/api/papers/' + paperId);
  const cur = await r0.json().catch(() => ({}));
  if (!r0.ok) { alert(cur.error || '读取试卷失败'); return false; }
  const ids = (cur.ids || []).filter(id => id !== questionId);
  if (!ids.length) { alert('试卷里至少留一道题'); return false; }
  const r = await fetch('/api/papers/' + paperId, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name: cur.name, ids: ids})
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) { alert(data.error || '移出试卷失败'); return false; }
  aiApplyPaper(data);
  return true;
}
function aiRenderVersion(base, ver) {
  const node = document.createElement('div');
  node.className = 'aiver';
  node.dataset.ver = String(ver.id);
  paintPick(node, !!ver.on_paper);
  const line = document.createElement('div');
  line.className = 'ailine';
  let cb = null;
  if (aiPaperId()) {
    cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.title = '放入这套试卷';
    cb.checked = !!ver.on_paper;
    cb.disabled = !ver.question_id;
    cb.addEventListener('change', async () => {
      cb.disabled = true;
      const ok = await aiSetOnPaper(base.base_question_id, ver.question_id, cb.checked);
      cb.disabled = !ver.question_id;
      if (!ok) cb.checked = !cb.checked;
      else if (cb.checked) {
        ver.on_paper = true;
        ver.in_bank = 1;
        const keep = node.querySelector('button.aikeep');
        if (keep) {
          keep.disabled = true;
          keep.textContent = '已收入题库';
        }
      } else {
        ver.on_paper = false;
      }
      paintPick(node, cb.checked);
    });
  }
  const seq = document.createElement('b');
  seq.textContent = 'V' + ver.seq;
  const st = document.createElement('span');
  st.className = 'aistat ' + aiStatusClass(ver.status);
  st.textContent = aiStatusText(ver.status);
  const parent = document.createElement('span');
  parent.textContent = ver.parent_version_id
    ? ('从 V' + (ver.parent_seq || '?') + ' 改来')
    : '从母题生成';
  const level = document.createElement('span');
  level.textContent = '改动：' + aiLevelText(ver.intensity);
  if (cb) line.appendChild(cb);
  line.appendChild(seq);
  line.appendChild(st);
  line.appendChild(parent);
  line.appendChild(level);
  node.appendChild(line);
  const cand = ver.candidate || {};
  if (cand.stem || (cand.segments && cand.segments.length)) {
    const stem = document.createElement('div');
    stem.className = 'stem';
    aiFillStem(stem, cand.segments, cand.stem);
    node.appendChild(stem);
  }
  if (cand.answer || cand.analysis) {
    const det = document.createElement('details');
    const sum = document.createElement('summary');
    sum.textContent = '答案 / 解析';
    det.appendChild(sum);
    const pre = document.createElement('div');
    pre.style.whiteSpace = 'pre-wrap';
    pre.textContent = [cand.answer, cand.analysis].filter(Boolean).join('\n\n');
    det.appendChild(pre);
    node.appendChild(det);
  }
  const judge = ver.judge || {};
  const issues = judge.issues || [];
  if (issues.length || judge.recommendation) {
    const det = document.createElement('details');
    const sum = document.createElement('summary');
    sum.textContent = '审核意见';
    det.appendChild(sum);
    issues.forEach(it => {
      const p = document.createElement('div');
      p.textContent = (it.type ? it.type + '：' : '') + (it.message || '');
      det.appendChild(p);
    });
    if (judge.recommendation) {
      const p = document.createElement('div');
      p.textContent = judge.recommendation;
      det.appendChild(p);
    }
    node.appendChild(det);
  }
  const canBranch = ver.status === 'PASS' || ver.status === 'SUSPECT' || ver.status === 'FAIL' || ver.status === 'JUDGE_ERROR';
  if (canBranch) {
    const box = document.createElement('div');
    box.className = 'airedit';
    const ta = document.createElement('textarea');
    ta.placeholder = '希望怎样改，可以留空';
    const row = document.createElement('div');
    row.className = 'row';
    const sel = aiIntensitySelect(ver.intensity || 'medium');
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.textContent = '基于这一版继续修改';
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      try {
        const payload = {
          parent_version_id: ver.id,
          feedback: ta.value,
          intensity: sel.value,
          mode: 'branch'
        };
        const paperId = aiPaperId();
        if (paperId) payload.paper_id = paperId;
        const r = await fetch('/api/questions/' + base.base_question_id + '/ai-variants', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload)
        });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) { alert(data.error || '没有开始修改'); return; }
        if (data.duplicate) alert('这道题正在生成，请稍候');
        aiStartPoll(aiPaperId());
        aiRefresh(aiPaperId());
      } finally {
        btn.disabled = false;
      }
    });
    row.appendChild(sel);
    row.appendChild(btn);
    box.appendChild(ta);
    box.appendChild(row);
    node.appendChild(box);
  }
  const actions = document.createElement('div');
  actions.className = 'row';
  if (ver.question_id) {
    const keep = document.createElement('button');
    keep.type = 'button';
    keep.className = 'secondary aikeep';
    keep.textContent = ver.in_bank ? '已收入题库' : '收入题库';
    keep.disabled = !!ver.in_bank;
    keep.addEventListener('click', async () => {
      keep.disabled = true;
      const r = await fetch('/api/ai-versions/' + ver.id + '/keep', {method: 'POST'});
      const data = await r.json().catch(() => ({}));
      if (!r.ok) { alert(data.error || '没有收入题库'); keep.disabled = false; return; }
      ver.in_bank = 1;
      keep.textContent = '已收入题库';
    });
    actions.appendChild(keep);
  }
  if (ver.status === 'GENERATION_ERROR') {
    const retry = document.createElement('button');
    retry.type = 'button';
    retry.textContent = '重试';
    retry.addEventListener('click', async () => {
      retry.disabled = true;
      const r = await fetch('/api/ai-versions/' + ver.id + '/retry', {method: 'POST'});
      const data = await r.json().catch(() => ({}));
      if (!r.ok) { alert(data.error || '没有重试'); retry.disabled = false; return; }
      aiStartPoll(aiPaperId());
      aiRefresh(aiPaperId());
    });
    actions.appendChild(retry);
  }
  if (ver.status === 'JUDGE_ERROR') {
    const again = document.createElement('button');
    again.type = 'button';
    again.textContent = '重新审核';
    again.addEventListener('click', async () => {
      again.disabled = true;
      const r = await fetch('/api/ai-versions/' + ver.id + '/rejudge', {method: 'POST'});
      const data = await r.json().catch(() => ({}));
      if (!r.ok) { alert(data.error || '没有重新审核'); again.disabled = false; return; }
      aiStartPoll(aiPaperId());
      aiRefresh(aiPaperId());
    });
    actions.appendChild(again);
  }
  if (actions.childNodes.length) node.appendChild(actions);
  if (ver.status === 'PASS' && !ver.excluded) {
    const pencil = document.createElement('button');
    pencil.type = 'button';
    pencil.className = 'pencil';
    pencil.title = '编辑题干';
    pencil.setAttribute('aria-label', '编辑题干');
    pencil.textContent = '✎';
    pencil.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const open = node.querySelector('.stemedit');
      if (open) {
        const ta0 = open.querySelector('textarea');
        if (ta0) ta0.focus();
        return;
      }
      const stemEl = node.querySelector('.stem');
      if (stemEl) stemEl.style.display = 'none';
      const box = document.createElement('div');
      box.className = 'stemedit';
      const ta = document.createElement('textarea');
      ta.value = cand.stem || '';
      const taA = document.createElement('textarea');
      taA.value = cand.answer || '';
      taA.placeholder = '答案';
      const row = document.createElement('div');
      row.className = 'row';
      const save = document.createElement('button');
      save.type = 'button';
      save.textContent = '保存';
      const cancel = document.createElement('button');
      cancel.type = 'button';
      cancel.className = 'secondary';
      cancel.textContent = '取消';
      cancel.addEventListener('click', () => {
        box.remove();
        if (stemEl) stemEl.style.display = '';
      });
      save.addEventListener('click', async () => {
        save.disabled = true;
        try {
          const r = await fetch('/api/ai-versions/' + ver.id + '/body', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({body: ta.value, answer: taA.value})
          });
          const data = await r.json().catch(() => ({}));
          if (!r.ok) { alert(data.error || '保存失败'); return; }
          ver.candidate = ver.candidate || {};
          ver.candidate.stem = data.body;
          ver.candidate.answer = data.answer;
          if (data.segments) ver.candidate.segments = data.segments;
          aiRefresh(aiPaperId());
        } finally {
          save.disabled = false;
        }
      });
      row.appendChild(save);
      row.appendChild(cancel);
      box.appendChild(ta);
      box.appendChild(taA);
      box.appendChild(row);
      if (stemEl) stemEl.insertAdjacentElement('afterend', box);
      else node.appendChild(box);
      ta.focus();
    });
    const trash = document.createElement('button');
    trash.type = 'button';
    trash.className = 'trash';
    trash.title = '删除这一版';
    trash.setAttribute('aria-label', '删除这一版');
    trash.textContent = '✕';
    trash.addEventListener('click', async (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (!window.confirm('确定删除这一版吗？')) return;
      trash.disabled = true;
      try {
        const r = await fetch('/api/ai-versions/' + ver.id, {method: 'DELETE'});
        const data = await r.json().catch(() => ({}));
        if (!r.ok) { alert(data.error || '删除失败'); return; }
        node.remove();
        aiRefresh(aiPaperId());
      } finally {
        trash.disabled = false;
      }
    });
    node.appendChild(pencil);
    node.appendChild(trash);
  }
  return node;
}
function aiUpsertBox(art, base) {
  const holder = art.querySelector('.chk > div') || art;
  let box = holder.querySelector(':scope > .aibox');
  if (!box) {
    box = document.createElement('div');
    box.className = 'aibox';
    box.addEventListener('click', e => e.stopPropagation());
    const head = document.createElement('div');
    head.className = 'aihead';
    const sel = aiIntensitySelect('medium');
    sel.className = 'ai-level';
    const go = document.createElement('button');
    go.type = 'button';
    go.textContent = '再生成一个版本';
    go.addEventListener('click', async () => {
      go.disabled = true;
      try {
        const payload = {
          parent_version_id: null,
          feedback: '',
          intensity: sel.value,
          mode: 'another'
        };
        const paperId = aiPaperId();
        if (paperId) payload.paper_id = paperId;
        const r = await fetch('/api/questions/' + base.base_question_id + '/ai-variants', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload)
        });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) { alert(data.error || '没有开始生成'); return; }
        if (data.duplicate) alert('这道题正在生成，请稍候');
        aiStartPoll(aiPaperId());
        aiRefresh(aiPaperId());
      } finally {
        go.disabled = false;
      }
    });
    const live = document.createElement('span');
    live.className = 'aistat run ailive';
    head.appendChild(go);
    head.appendChild(sel);
    head.appendChild(live);
    const note = document.createElement('div');
    note.className = 'ainote';
    note.textContent = '「已校验」只表示自动检查通过，不是最终定稿。';
    const progress = document.createElement('div');
    progress.className = 'aiprogress';
    progress.setAttribute('role', 'status');
    progress.setAttribute('aria-live', 'polite');
    progress.hidden = true;
    const prof = document.createElement('div');
    prof.className = 'aiprof';
    const vers = document.createElement('div');
    vers.className = 'aivers';
    box.appendChild(head);
    box.appendChild(progress);
    box.appendChild(note);
    box.appendChild(prof);
    box.appendChild(vers);
    holder.appendChild(box);
  }
  const live = box.querySelector('.ailive');
  const state = aiProgressText(base.active_job);
  if (live) {
    live.textContent = state ? state.title : '';
  }
  const progress = box.querySelector('.aiprogress');
  if (progress) {
    progress.hidden = !state;
    const explanation = state ? state.detail + ' ' + (base.active_job.service_message || '') + ' 一道新题通常要先读懂原题、再改写、最后检查。带图题有时需要一分钟或更久。页面会自动更新，无需重复点击。' : '';
    if (progress.textContent !== explanation) progress.textContent = explanation;
  }
  const prof = box.querySelector('.aiprof');
  if (prof) {
    const s = base.profile_summary;
    prof.textContent = '';
    if (s) {
      const bits = [];
      if (s.knowledge) bits.push('考点：' + s.knowledge);
      if (s.skill) bits.push('技能：' + s.skill);
      if (s.difficulty) bits.push('难度：' + s.difficulty);
      prof.textContent = bits.join('　');
    }
  }
  const vers = box.querySelector('.aivers');
  let notes = box.querySelector('.ainotices');
  if (!notes) {
    notes = document.createElement('div');
    notes.className = 'ainotices';
    vers.parentNode.insertBefore(notes, vers);
  }
  notes.textContent = '';
  (base.notices || []).forEach(n => {
    if (!n || !n.message) return;
    const line = document.createElement('div');
    line.className = n.stopped ? 'aistop' : 'ainote';
    line.textContent = n.stopped ? AI_STOP_NOTICE : (n.error ? aiFriendlyError(n.message) : AI_RETRY_NOTICE);
    notes.appendChild(line);
  });
  const seen = {};
  (base.versions || []).forEach(ver => {
    if (!ver || ver.status !== 'PASS' || ver.excluded) return;
    seen[String(ver.id)] = true;
    let node = vers.querySelector('.aiver[data-ver="' + ver.id + '"]');
    const focused = node && node.contains(document.activeElement);
    if (!node) {
      vers.appendChild(aiRenderVersion(base, ver));
    } else if (!focused) {
      node.replaceWith(aiRenderVersion(base, ver));
    } else {
      const st = node.querySelector('.aistat');
      if (st) {
        st.className = 'aistat ' + aiStatusClass(ver.status);
        st.textContent = aiStatusText(ver.status);
      }
    }
  });
  vers.querySelectorAll('.aiver').forEach(node => {
    if (!seen[node.dataset.ver]) node.remove();
  });
}
async function aiRefresh(paperId) {
  const ids = [];
  list.querySelectorAll('article[data-qid]').forEach(art => {
    if ((art.dataset.origin || 'human') === 'ai') return;
    const id = Number(art.dataset.qid);
    if (id) ids.push(id);
  });
  if (!ids.length) return false;
  const u = new URL('/api/questions/ai-status', location.origin);
  u.searchParams.set('ids', ids.join(','));
  const pid = paperId || aiPaperId();
  if (pid) u.searchParams.set('paper_id', String(pid));
  const r = await fetch(u);
  if (!r.ok) return false;
  const data = await r.json().catch(() => ({}));
  (data.bases || []).forEach(base => {
    const art = list.querySelector('article[data-qid="' + base.base_question_id + '"]');
    if (art) aiUpsertBox(art, base);
  });
  return !!data.active;
}
function aiStartPoll(paperId) {
  if (aiPollTimer) return;
  aiPollPaper = paperId || aiPaperId() || null;
  aiPollTimer = setInterval(async () => {
    try {
      const live = await aiRefresh(aiPaperId());
      if (!live) window.chemBankStopAi();
    } catch (e) {}
  }, 1500);
}
window.chemBankStopAi = function() {
  if (aiPollTimer) clearInterval(aiPollTimer);
  aiPollTimer = null;
};
window.chemBankMountAi = async function() {
  try {
    const live = await aiRefresh(aiPaperId());
    if (live) aiStartPoll(aiPaperId());
  } catch (e) {}
};
window.chemBankAiCompose = async function(detail) {
  const paperId = detail && detail.paperId;
  if (!paperId) return;
  const sel = document.getElementById('ai-intensity');
  const intensity = sel ? sel.value : 'medium';
  if (!paperFilter || paperFilter.id !== paperId) {
    await openPaper(paperId, {view: true});
  }
  const r = await fetch('/api/papers/' + paperId + '/ai-variants', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      intensity: intensity,
      question_ids: (detail.questionIds || []).slice()
    })
  });
  const data = await r.json().catch(() => ({}));
  const msg = document.getElementById('ai-msg');
  if (!r.ok) { alert(data.error || '没有开始生成'); return; }
  const jobs = data.jobs || [];
  const fresh = jobs.filter(j => !j.duplicate).length;
  const dup = jobs.filter(j => j.duplicate).length;
  if (msg) {
    if (!jobs.length) msg.textContent = '这套试卷上没有可以生成变式的题目';
    else msg.textContent = '已提交 ' + fresh + ' 题' + (dup ? ('，另有 ' + dup + ' 题正在处理') : '') + '。每题会先读懂、再改写、最后检查；题目多时需要分批等待，具体进度见各题下方。';
  }
  if (jobs.length) aiStartPoll(paperId);
  aiRefresh(paperId);
};
document.getElementById('ai-stop-all').addEventListener('click', async () => {
  const btn = document.getElementById('ai-stop-all');
  const msg = document.getElementById('ai-msg');
  if (!btn || btn.disabled) return;
  btn.disabled = true;
  try {
    const r = await fetch('/api/ai/stop-all', {method: 'POST'});
    if (!r.ok) {
      if (msg) msg.textContent = '没能叫停';
      return;
    }
    if (msg) msg.textContent = '已叫停';
    try {
      const live = await aiRefresh(aiPaperId());
      if (!live) window.chemBankStopAi();
    } catch (e) {}
  } catch (e) {
    if (msg) msg.textContent = '没能叫停';
  } finally {
    btn.disabled = false;
  }
});

paperReady = true;
const savedPaper = new URL(location.href).searchParams.get('paper');
if (savedPaper) openPaper(savedPaper, {view: new URL(location.href).searchParams.get('view') === '1'});
else loadTree().then(load);
</script>
</body>
</html>
"""



def _ai_body(raw):
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _ai_send(handler, code, payload):
    handler._send(code, json.dumps(payload, ensure_ascii=False), "application/json; charset=utf-8")


def _handle_ai_post(handler, path, raw):
    if os.environ.get("CHEM_DISABLE_AI") == "1" and (
        path.endswith("/ai-variants") or path.startswith("/api/ai-versions/")
        or path.endswith("/ai-generate") or path.endswith("/ai-regenerate")
        or path.endswith("/ai-branch")):
        _ai_send(handler, 400, {"error": "隔离开发预览已停用 AI，请在正式环境配置后使用"})
        return True
    if path == "/api/ai/stop-all":
        with LOCK:
            con = db()
            try:
                aivariant.ensure_schema(con)
                result = aivariant.stop_all(con)
            finally:
                con.close()
        _ai_send(handler, 200, result)
        return True
    m = re.fullmatch(r"/api/papers/(\d+)/ai-variants", path)
    if m:
        data = _ai_body(raw)
        if data is None:
            _ai_send(handler, 400, {"error": "请求格式不对"})
            return True
        with LOCK:
            con = db()
            try:
                aivariant.ensure_schema(con)
                result, err = aivariant.enqueue_paper(
                    con, int(m.group(1)), data.get("intensity"), data.get("question_ids")
                )
            finally:
                con.close()
        if err == "试卷不存在":
            _ai_send(handler, 404, {"error": err})
            return True
        if err:
            _ai_send(handler, 400, {"error": err})
            return True
        _ai_send(handler, 200, result)
        return True
    m = re.fullmatch(r"/api/papers/(\d+)/items", path)
    if m:
        data = _ai_body(raw)
        if data is None or "question_id" not in data:
            _ai_send(handler, 400, {"error": "请求格式不对"})
            return True
        with LOCK:
            con = db()
            try:
                paper, err = banklib.insert_question_after(
                    con, int(m.group(1)), data.get("question_id"), data.get("after_question_id")
                )
                if not err and paper:
                    con.execute(
                        "UPDATE questions SET in_bank=1 WHERE id=?",
                        (int(data.get("question_id")),),
                    )
                    con.commit()
                    paper = banklib.get_paper(con, int(m.group(1)))
            finally:
                con.close()
        if err in ("试卷不存在", "题目不存在"):
            _ai_send(handler, 404, {"error": err})
            return True
        if err:
            _ai_send(handler, 400, {"error": err})
            return True
        _ai_send(handler, 200, paper)
        return True
    m = re.fullmatch(r"/api/questions/(\d+)/ai-variants", path)
    if m:
        data = _ai_body(raw)
        if data is None:
            _ai_send(handler, 400, {"error": "请求格式不对"})
            return True
        with LOCK:
            con = db()
            try:
                aivariant.ensure_schema(con)
                result, err = aivariant.enqueue_base(
                    con,
                    data.get("paper_id"),
                    int(m.group(1)),
                    data.get("parent_version_id"),
                    data.get("feedback"),
                    data.get("intensity"),
                    data.get("mode") or "another",
                )
            finally:
                con.close()
        if err in ("题目不存在", "试卷不存在", "版本不存在", "找不到要修改的版本"):
            _ai_send(handler, 404, {"error": err})
            return True
        if err:
            _ai_send(handler, 400, {"error": err})
            return True
        _ai_send(handler, 200, result)
        return True
    m = re.fullmatch(r"/api/ai-versions/(\d+)/(rejudge|retry|keep)", path)
    if m:
        action = m.group(2)
        with LOCK:
            con = db()
            try:
                aivariant.ensure_schema(con)
                vid = int(m.group(1))
                if action == "rejudge":
                    result, err = aivariant.rejudge_version(con, vid)
                elif action == "retry":
                    result, err = aivariant.retry_version(con, vid)
                else:
                    result, err = aivariant.keep_version(con, vid)
            finally:
                con.close()
        if err in ("版本不存在",):
            _ai_send(handler, 404, {"error": err})
            return True
        if err:
            _ai_send(handler, 400, {"error": err})
            return True
        _ai_send(handler, 200, result)
        return True
    m = re.fullmatch(r"/api/ai-versions/(\d+)/body", path)
    if m:
        data = _ai_body(raw)
        if data is None:
            _ai_send(handler, 400, {"error": "请求格式不对"})
            return True
        with LOCK:
            con = db()
            try:
                aivariant.ensure_schema(con)
                result, err = aivariant.update_version_text(con, int(m.group(1)), data)
            finally:
                con.close()
        if err in ("版本不存在", "题目不存在"):
            _ai_send(handler, 404, {"error": err})
            return True
        if err:
            _ai_send(handler, 400, {"error": err})
            return True
        _ai_send(handler, 200, result)
        return True
    return False


def db():
    con = sqlite3.connect(banklib.DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=30000")
    return con


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, code, body, content_type, extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _handle_bot(self, method, u):
        try:
            port = self.server.server_address[1]
            host = urlparse('http://' + self.headers.get('Host', ''))
            if host.hostname not in ('127.0.0.1', 'localhost') or host.port != port:
                raise bot_bridge.BridgeError('组卷工具只接受本机题库地址', 403)
            origin = self.headers.get('Origin')
            if origin:
                parsed = urlparse(origin)
                if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost') or parsed.port != port:
                    raise bot_bridge.BridgeError('请从本机题库程序使用组卷工具', 403)
            actions = {'/api/bot/search': bot_bridge.search,
                       '/api/bot/questions': bot_bridge.read_questions,
                       '/api/bot/papers': bot_bridge.create_paper}
            if method == 'GET' and u.path == '/api/bot/info':
                with LOCK:
                    con = db()
                    try:
                        result = bot_bridge.info(con)
                    finally:
                        con.close()
            elif method == 'POST' and u.path in actions:
                if self.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
                    raise bot_bridge.BridgeError('组卷工具需要 application/json 请求', 415)
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 131072:
                        raise bot_bridge.BridgeError('请求内容为空或超过大小限制')
                    payload = json.loads(self.rfile.read(length).decode('utf-8'))
                except (ValueError, UnicodeError):
                    raise bot_bridge.BridgeError('请求不是有效的 UTF-8 JSON')
                with LOCK:
                    con = db()
                    try:
                        result = actions[u.path](con, payload)
                    finally:
                        con.close()
            else:
                raise bot_bridge.BridgeError('组卷工具没有这个操作', 404)
            self._send(200, json.dumps(result, ensure_ascii=False), 'application/json; charset=utf-8')
        except bot_bridge.BridgeError as exc:
            self.close_connection = True
            self._send(exc.status, json.dumps({'error': str(exc)}, ensure_ascii=False), 'application/json; charset=utf-8')
        except (ValueError, sqlite3.Error):
            self.close_connection = True
            self._send(400, json.dumps({'error': '无法读取题库，请确认程序使用的是已准备好的题库'}, ensure_ascii=False), 'application/json; charset=utf-8')

    def do_GET(self):
        u = urlparse(self.path)
        if u.path.startswith('/api/bot/'):
            self._handle_bot('GET', u)
            return
        if u.path in ("/", "/index.html"):
            page = PAGE
            if os.environ.get("CHEM_DISABLE_AI") == "1":
                page = page.replace("天津 · 个人题库 · 勾选题目后可导出 Word 试卷",
                                    "隔离开发预览 · 使用独立题库 · AI 已停用")
            self._send(200, page, "text/html; charset=utf-8")
            return
        if u.path == "/api/curriculum":
            self._send(200, json.dumps(question_analysis.CURRICULUM, ensure_ascii=False), "application/json; charset=utf-8")
            return
        if u.path == "/api/categories":
            with LOCK:
                con = db()
                tree = banklib.category_tree(con)
                con.close()
            payload = {"tree": tree, "threshold": banklib.THRESHOLD}
            self._send(200, json.dumps(payload, ensure_ascii=False), "application/json; charset=utf-8")
            return
        if u.path == "/api/stats":
            with LOCK:
                con = db()
                st = banklib.compute_stats(con)
                merged = con.execute(
                    "SELECT COALESCE(SUM(src_n - 1), 0) FROM (SELECT question_id, COUNT(*) src_n FROM sources GROUP BY question_id)"
                ).fetchone()[0]
                con.close()
            st["duplicates_merged"] = merged
            self._send(200, json.dumps(st, ensure_ascii=False), "application/json; charset=utf-8")
            return
        if u.path == "/api/papers":
            with LOCK:
                con = db()
                papers = banklib.list_papers(con)
                con.close()
            self._send(200, json.dumps({"papers": papers}, ensure_ascii=False), "application/json; charset=utf-8")
            return
        mai = re.fullmatch(r"/api/papers/(\d+)/ai-compose", u.path)
        if mai:
            with LOCK:
                con = db()
                item = banklib.get_paper(con, int(mai.group(1)))
                con.close()
            if not item:
                self._send(404, json.dumps({"error": "试卷不存在"}, ensure_ascii=False), "application/json; charset=utf-8")
                return
            payload = {"paper_id": item["id"], "name": item["name"], "question_ids": item["ids"]}
            self._send(200, json.dumps(payload, ensure_ascii=False), "application/json; charset=utf-8")
            return
        mstatus = re.fullmatch(r"/api/papers/(\d+)/ai-status", u.path)
        if mstatus:
            with LOCK:
                con = db()
                try:
                    payload = aivariant.paper_status(con, int(mstatus.group(1)))
                finally:
                    con.close()
            if payload is None:
                self._send(404, json.dumps({"error": "试卷不存在"}, ensure_ascii=False), "application/json; charset=utf-8")
                return
            self._send(200, json.dumps(payload, ensure_ascii=False), "application/json; charset=utf-8")
            return
        mpaper = re.fullmatch(r"/api/papers/(\d+)", u.path)
        if mpaper:
            with LOCK:
                con = db()
                item = banklib.get_paper(con, int(mpaper.group(1)))
                con.close()
            if not item:
                self._send(404, json.dumps({"error": "试卷不存在"}, ensure_ascii=False), "application/json; charset=utf-8")
                return
            self._send(200, json.dumps(item, ensure_ascii=False), "application/json; charset=utf-8")
            return
        if u.path == "/api/questions":
            qs = parse_qs(u.query, keep_blank_values=True)
            paper_mode = "ids" in qs
            id_list = []
            if paper_mode:
                seen_ids = set()
                for part in (qs.get("ids") or [""])[0].split(","):
                    part = part.strip()
                    if not part:
                        continue
                    try:
                        qid = int(part)
                    except ValueError:
                        continue
                    if qid not in seen_ids:
                        seen_ids.add(qid)
                        id_list.append(qid)
                id_list = id_list[:200]
            q = (qs.get("q") or [""])[0].strip()
            major = (qs.get("major") or [""])[0].strip()
            minor = (qs.get("minor") or [""])[0].strip()
            try:
                offset = max(0, int((qs.get("offset") or ["0"])[0]))
                limit = min(50, max(1, int((qs.get("limit") or ["20"])[0])))
            except ValueError:
                offset, limit = 0, 20
            if paper_mode:
                if id_list:
                    sql = "SELECT * FROM questions WHERE id IN (%s)" % ",".join("?" * len(id_list))
                    args = list(id_list)
                else:
                    sql = "SELECT * FROM questions WHERE 0"
                    args = []
                offset, limit = 0, max(1, len(id_list))
            else:
                sql = "SELECT * FROM questions WHERE " + banklib.questions_visible_clause()
                args = []
            if q and not paper_mode:
                sql += (
                    " AND (body LIKE ? ESCAPE '\\' OR answer LIKE ? ESCAPE '\\'"
                    " OR id IN (SELECT question_id FROM sources"
                    " WHERE rel_path LIKE ? ESCAPE '\\' OR orig_qnum LIKE ? ESCAPE '\\'"
                    " OR (rel_path || '（原题号 ' || orig_qnum || '）') LIKE ? ESCAPE '\\'))"
                )
                like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                args.extend([like, like, like, like, like])
            if major and not paper_mode:
                sql += " AND (major=? OR id IN (SELECT question_id FROM question_majors WHERE major=?))"
                args.extend([major, major])
            if minor and not paper_mode:
                sql += " AND (minor=? OR id IN (SELECT question_id FROM question_minors WHERE minor=?))"
                args.extend([minor, minor])
            if not paper_mode:
                theme = (qs.get("theme") or [""])[0]
                point_id = (qs.get("knowledge") or [""])[0]
                if theme or point_id:
                    points = [p for p in question_analysis.CURRICULUM["points"]
                              if (not theme or p["theme"] == theme) and (not point_id or p["id"] == point_id)]
                    keywords = list(dict.fromkeys(kw for p in points for kw in p["keywords"]))
                    if keywords:
                        sql += " AND (" + " OR ".join("instr(lower(body), lower(?)) > 0" for kw in keywords) + ")"
                        args.extend(keywords)
                    else:
                        sql += " AND 0"
            types = [x.strip() for x in (qs.get("types") or [""])[0].split(",") if x.strip()]
            types = [x for x in types if x in banklib.QTYPES]
            if types and not paper_mode:
                sql += " AND qtype IN (%s)" % ",".join("?" * len(types))
                args.extend(types)
            with LOCK:
                con = db()
                if paper_mode:
                    rows = con.execute(sql, args).fetchall() if id_list else []
                    by_id = {row["id"]: row for row in rows}
                    rows = [by_id[i] for i in id_list if i in by_id]
                    total = len(rows)
                else:
                    total = con.execute("SELECT COUNT(*) FROM (" + sql + ")", args).fetchone()[0]
                    rows = con.execute(sql + " ORDER BY id LIMIT ? OFFSET ?", args + [limit, offset]).fetchall()
                items = []
                for row in rows:
                    source_details = banklib.source_details(con, row["id"])
                    sources = [source["label"] for source in source_details]
                    majors, minors = banklib.load_labels(con, row["id"], row["major"], row["minor"])
                    items.append({
                        "id": row["id"],
                        "metadata": banklib.question_metadata(con, row),
                        "qnum": row["qnum"],
                        "body": row["body"],
                        "answer": row["answer"],
                        "segments": json.loads(row["segments"] or "[]"),
                        "major": row["major"],
                        "minor": row["minor"],
                        "majors": majors,
                        "minors": minors,
                        "image_count": row["image_count"],
                        "qtype": row["qtype"] if "qtype" in row.keys() else "",
                        "qtype_manual": row["qtype_manual"] if "qtype_manual" in row.keys() else 0,
                        "body_manual": row["body_manual"] if "body_manual" in row.keys() else 0,
                        "category_manual": row["category_manual"] if "category_manual" in row.keys() else 0,
                        "sources": sources,
                        "source_details": source_details,
                        "origin": (row["origin"] if "origin" in row.keys() and row["origin"] else "human"),
                        "in_bank": (0 if "in_bank" in row.keys() and row["in_bank"] is not None and int(row["in_bank"]) == 0 else 1),
                        "base_question_id": (row["base_question_id"] if "base_question_id" in row.keys() else None),
                        "ai_version_id": (row["ai_version_id"] if "ai_version_id" in row.keys() else None),
                    })
                n = con.execute("SELECT COUNT(*) FROM questions WHERE " + banklib.questions_visible_clause()).fetchone()[0]
                con.close()
            payload = {
                "total": total,
                "offset": offset,
                "items": items,
                "stats_line": "题库共 %d 题（当前筛选 %d 题）" % (n, total),
            }
            self._send(200, json.dumps(payload, ensure_ascii=False), "application/json; charset=utf-8")
            return
        if u.path.startswith("/media/"):
            name = os.path.basename(u.path)
            if not re.fullmatch(r"[0-9a-f]{64}\.(png|jpg|gif|bmp|webp|wmf|emf)", name):
                self._send(404, "not found", "text/plain")
                return
            path = os.path.join(banklib.MEDIA, name)
            if not os.path.isfile(path):
                self._send(404, "not found", "text/plain")
                return
            ext = name.rsplit(".", 1)[-1]
            ctype = {
                "png": "image/png", "jpg": "image/jpeg", "gif": "image/gif",
                "bmp": "image/bmp", "webp": "image/webp", "wmf": "image/wmf", "emf": "image/emf",
            }.get(ext, "application/octet-stream")
            with open(path, "rb") as f:
                data = f.read()
            self._send(200, data, ctype, {"Cache-Control": "public, max-age=86400"})
            return
        if u.path == "/api/questions/ai-status":
            qs = parse_qs(u.query, keep_blank_values=True)
            id_list = []
            seen_ids = set()
            for part in (qs.get("ids") or [""])[0].split(","):
                part = part.strip()
                if not part:
                    continue
                try:
                    qid = int(part)
                except ValueError:
                    continue
                if qid not in seen_ids:
                    seen_ids.add(qid)
                    id_list.append(qid)
            id_list = id_list[:200]
            paper_raw = (qs.get("paper_id") or [""])[0].strip()
            paper_id = None
            if paper_raw:
                try:
                    paper_id = int(paper_raw)
                except ValueError:
                    paper_id = None
            with LOCK:
                con = db()
                try:
                    aivariant.ensure_schema(con)
                    payload = aivariant.questions_status(con, id_list, paper_id)
                finally:
                    con.close()
            self._send(200, json.dumps(payload, ensure_ascii=False), "application/json; charset=utf-8")
            return
        if u.path.startswith("/api/questions/"):
            try:
                qid = int(u.path.rsplit("/", 1)[-1])
            except ValueError:
                self._send(404, "not found", "text/plain")
                return
            with LOCK:
                con = db()
                row = con.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone()
                if not row:
                    con.close()
                    self._send(404, json.dumps({"error": "not found"}), "application/json")
                    return
                source_details = banklib.source_details(con, qid)
                sources = [source["label"] for source in source_details]
                metadata = banklib.question_metadata(con, row)
                con.close()
            payload = {
                "id": row["id"], "body": row["body"], "answer": row["answer"],
                "metadata": metadata,
                "segments": json.loads(row["segments"] or "[]"),
                "major": row["major"], "minor": row["minor"],
                "qtype": row["qtype"] if "qtype" in row.keys() else "",
                "qtype_manual": row["qtype_manual"] if "qtype_manual" in row.keys() else 0,
                "body_manual": row["body_manual"] if "body_manual" in row.keys() else 0,
                "category_manual": row["category_manual"] if "category_manual" in row.keys() else 0,
                "sources": sources, "source_details": source_details, "image_count": row["image_count"],
                "origin": (row["origin"] if "origin" in row.keys() and row["origin"] else "human"),
                "in_bank": (0 if "in_bank" in row.keys() and row["in_bank"] is not None and int(row["in_bank"]) == 0 else 1),
                "base_question_id": (row["base_question_id"] if "base_question_id" in row.keys() else None),
                "ai_version_id": (row["ai_version_id"] if "ai_version_id" in row.keys() else None),
            }
            self._send(200, json.dumps(payload, ensure_ascii=False), "application/json; charset=utf-8")
            return
        self._send(404, "not found", "text/plain; charset=utf-8")

    def do_POST(self):
        u = urlparse(self.path)
        if u.path.startswith('/api/bot/'):
            self._handle_bot('POST', u)
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if _handle_ai_post(self, u.path, raw):
            return
        mmove = re.fullmatch(r"/api/papers/(\d+)/move", u.path)
        if mmove:
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                self._send(400, json.dumps({"error": "请求格式不对"}, ensure_ascii=False), "application/json; charset=utf-8")
                return
            if not isinstance(payload, dict):
                self._send(400, json.dumps({"error": "请求格式不对"}, ensure_ascii=False), "application/json; charset=utf-8")
                return
            with LOCK:
                con = db()
                try:
                    result, err = banklib.move_paper_question(
                        con, int(mmove.group(1)), payload.get("question_id"), payload.get("dir")
                    )
                finally:
                    con.close()
            if err == "试卷不存在":
                self._send(404, json.dumps({"error": err}, ensure_ascii=False), "application/json; charset=utf-8")
                return
            if err:
                self._send(400, json.dumps({"error": err}, ensure_ascii=False), "application/json; charset=utf-8")
                return
            self._send(200, json.dumps(result, ensure_ascii=False), "application/json; charset=utf-8")
            return
        if u.path == "/api/papers/assign":
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                self._send(400, json.dumps({"error": "请求格式不对"}, ensure_ascii=False), "application/json; charset=utf-8")
                return
            if not isinstance(payload, dict):
                self._send(400, json.dumps({"error": "请求格式不对"}, ensure_ascii=False), "application/json; charset=utf-8")
                return
            with LOCK:
                con = db()
                try:
                    result, err = banklib.assign_questions_to_papers(
                        con, payload.get("question_ids") or [], payload.get("paper_ids") or []
                    )
                finally:
                    con.close()
            if err == "试卷不存在":
                self._send(404, json.dumps({"error": err}, ensure_ascii=False), "application/json; charset=utf-8")
                return
            if err:
                self._send(400, json.dumps({"error": err}, ensure_ascii=False), "application/json; charset=utf-8")
                return
            self._send(200, json.dumps(result, ensure_ascii=False), "application/json; charset=utf-8")
            return
        mpaper = re.fullmatch(r"/api/papers(?:/(\d+))?", u.path)
        if mpaper:
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                self._send(400, json.dumps({"error": "请求格式不对"}, ensure_ascii=False), "application/json; charset=utf-8")
                return
            if not isinstance(payload, dict):
                self._send(400, json.dumps({"error": "请求格式不对"}, ensure_ascii=False), "application/json; charset=utf-8")
                return
            paper_id = int(mpaper.group(1)) if mpaper.group(1) else None
            with LOCK:
                con = db()
                try:
                    result, err = banklib.apply_paper(con, payload, paper_id)
                finally:
                    con.close()
            if err == "试卷不存在":
                self._send(404, json.dumps({"error": err}, ensure_ascii=False), "application/json; charset=utf-8")
                return
            if err:
                self._send(400, json.dumps({"error": err}, ensure_ascii=False), "application/json; charset=utf-8")
                return
            self._send(200, json.dumps(result, ensure_ascii=False), "application/json; charset=utf-8")
            return
        if u.path == "/api/questions":
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                self._send(400, json.dumps({"error": "请求格式不对"}, ensure_ascii=False), "application/json; charset=utf-8")
                return
            with LOCK:
                con = db()
                try:
                    result, err = banklib.insert_handwritten(con, payload)
                finally:
                    con.close()
            if err == "试卷不存在":
                self._send(404, json.dumps({"error": err}, ensure_ascii=False), "application/json; charset=utf-8")
                return
            if err:
                self._send(400, json.dumps({"error": err}, ensure_ascii=False), "application/json; charset=utf-8")
                return
            self._send(200, json.dumps(result, ensure_ascii=False), "application/json; charset=utf-8")
            return
        if u.path == "/api/export":
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
                ids = payload.get("ids") or []
                ids = list(dict.fromkeys(int(i) for i in ids))
                if len(ids) > 500:
                    self._send(400, json.dumps({"error": "单次最多导出 500 题，请分成多套试卷"}, ensure_ascii=False), "application/json; charset=utf-8")
                    return
                keep_source = bool(payload.get("keep_source"))
                auto_number = True if "auto_number" not in payload else bool(payload.get("auto_number"))
                keep_answers = payload.get("keep_answers", False)
                if not isinstance(keep_answers, bool):
                    raise ValueError('keep_answers must be boolean')
            except Exception:
                self._send(400, json.dumps({"error": "bad request"}), "application/json")
                return
            if not ids:
                self._send(400, json.dumps({"error": "no ids"}), "application/json")
                return
            dest = os.path.join(banklib.DATA_DIR, "last-export.docx")
            try:
                with LOCK:
                    n = banklib.export_docx(ids, dest, keep_source=keep_source, auto_number=auto_number, keep_answers=keep_answers)
                    with open(dest, "rb") as f:
                        data = f.read()
            except RuntimeError as exc:
                self._send(400, json.dumps({"error": str(exc)}, ensure_ascii=False), "application/json; charset=utf-8")
                return
            self._send(
                200, data,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                {"Content-Disposition": "attachment; filename=\"paper.docx\""},
            )
            return
        if u.path == "/api/import":
            ctype = self.headers.get("Content-Type") or ""
            m = re.search(r'boundary=(?:"([^"]+)"|([^;]+))', ctype)
            if not m:
                self._send(400, json.dumps({"error": "需要上传 doc/docx"}), "application/json")
                return
            boundary = (m.group(1) or m.group(2)).strip().encode()
            filename = None
            filebytes = None
            paper_id = None
            for part in raw.split(b"--" + boundary):
                if b"Content-Disposition" not in part:
                    continue
                header, _, data = part.partition(b"\r\n\r\n")
                if data.endswith(b"\r\n"):
                    data = data[:-2]
                disp = header.decode("utf-8", "replace")
                nm = re.search(r'name="([^"]*)"', disp)
                field = nm.group(1) if nm else ""
                fm = re.search(r'filename="([^"]*)"', disp)
                if field == "paper_id" and not (fm and fm.group(1)):
                    paper_id = data.decode("utf-8", "replace").strip()
                    continue
                if fm and fm.group(1):
                    filename = os.path.basename(fm.group(1).replace("\\", "/"))
                    filebytes = data
            if not filename or filebytes is None:
                self._send(400, json.dumps({"error": "没有文件"}), "application/json; charset=utf-8")
                return
            low = filename.lower()
            if not (low.endswith(".doc") or low.endswith(".docx")):
                self._send(400, json.dumps({"error": "只接受 .doc / .docx"}), "application/json; charset=utf-8")
                return
            os.makedirs(banklib.IMPORT_DIR, exist_ok=True)
            dest = os.path.join(banklib.IMPORT_DIR, filename)
            with open(dest, "wb") as f:
                f.write(filebytes)
            rel = "imports/" + filename
            try:
                with LOCK:
                    info = banklib.import_one(dest, rel, paper_id or None)
            except LookupError as e:
                self._send(404, json.dumps({"error": str(e)}, ensure_ascii=False), "application/json; charset=utf-8")
                return
            except Exception as e:
                self._send(400, json.dumps({"error": str(e)}, ensure_ascii=False), "application/json; charset=utf-8")
                return
            self._send(200, json.dumps(info, ensure_ascii=False), "application/json; charset=utf-8")
            return
        m = re.fullmatch(r"/api/questions/(\d+)/category", u.path)
        if m:
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                self._send(400, json.dumps({"error": "请求格式不对"}, ensure_ascii=False), "application/json; charset=utf-8")
                return
            qid = int(m.group(1))
            with LOCK:
                con = db()
                try:
                    result, err = banklib.assign_category(con, qid, payload)
                finally:
                    con.close()
            if err == "题目不存在":
                self._send(404, json.dumps({"error": err}, ensure_ascii=False), "application/json; charset=utf-8")
                return
            if err:
                self._send(400, json.dumps({"error": err}, ensure_ascii=False), "application/json; charset=utf-8")
                return
            self._send(200, json.dumps(result, ensure_ascii=False), "application/json; charset=utf-8")
            return
        m = re.fullmatch(r"/api/questions/(\d+)/qtype", u.path)
        if m:
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                self._send(400, json.dumps({"error": "请求格式不对"}, ensure_ascii=False), "application/json; charset=utf-8")
                return
            qid = int(m.group(1))
            with LOCK:
                con = db()
                try:
                    result, err = banklib.assign_qtype(con, qid, payload)
                finally:
                    con.close()
            if err == "题目不存在":
                self._send(404, json.dumps({"error": err}, ensure_ascii=False), "application/json; charset=utf-8")
                return
            if err:
                self._send(400, json.dumps({"error": err}, ensure_ascii=False), "application/json; charset=utf-8")
                return
            self._send(200, json.dumps(result, ensure_ascii=False), "application/json; charset=utf-8")
            return
        m = re.fullmatch(r"/api/questions/(\d+)/split", u.path)
        if m:
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                self._send(400, json.dumps({"error": "请求格式不对"}, ensure_ascii=False), "application/json; charset=utf-8")
                return
            qid = int(m.group(1))
            with LOCK:
                con = db()
                try:
                    result, err = banklib.split_question(con, qid, payload)
                finally:
                    con.close()
            if err == "题目不存在" or err == "试卷不存在":
                self._send(404, json.dumps({"error": err}, ensure_ascii=False), "application/json; charset=utf-8")
                return
            if err:
                self._send(400, json.dumps({"error": err}, ensure_ascii=False), "application/json; charset=utf-8")
                return
            self._send(200, json.dumps(result, ensure_ascii=False), "application/json; charset=utf-8")
            return
        m = re.fullmatch(r"/api/questions/(\d+)/body", u.path)
        if m:
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                self._send(400, json.dumps({"error": "请求格式不对"}, ensure_ascii=False), "application/json; charset=utf-8")
                return
            qid = int(m.group(1))
            with LOCK:
                con = db()
                try:
                    result, err = banklib.assign_body(con, qid, payload)
                finally:
                    con.close()
            if err == "题目不存在":
                self._send(404, json.dumps({"error": err}, ensure_ascii=False), "application/json; charset=utf-8")
                return
            if err:
                self._send(400, json.dumps({"error": err}, ensure_ascii=False), "application/json; charset=utf-8")
                return
            self._send(200, json.dumps(result, ensure_ascii=False), "application/json; charset=utf-8")
            return
        self._send(404, "not found", "text/plain")

    def do_DELETE(self):
        u = urlparse(self.path)
        mver = re.fullmatch(r"/api/ai-versions/(\d+)", u.path)
        if mver:
            with LOCK:
                con = db()
                try:
                    aivariant.ensure_schema(con)
                    result, err = aivariant.exclude_version(con, int(mver.group(1)))
                finally:
                    con.close()
            if err:
                self._send(404, json.dumps({"error": err}, ensure_ascii=False), "application/json; charset=utf-8")
                return
            self._send(200, json.dumps(result, ensure_ascii=False), "application/json; charset=utf-8")
            return
        mq = re.fullmatch(r"/api/questions/(\d+)", u.path)
        if mq:
            with LOCK:
                con = db()
                try:
                    ok = banklib.delete_question(con, int(mq.group(1)))
                finally:
                    con.close()
            if not ok:
                self._send(404, json.dumps({"error": "题目不存在"}, ensure_ascii=False), "application/json; charset=utf-8")
                return
            self._send(200, json.dumps({"ok": True}, ensure_ascii=False), "application/json; charset=utf-8")
            return
        mpaper = re.fullmatch(r"/api/papers/(\d+)", u.path)
        if not mpaper:
            self._send(404, "not found", "text/plain")
            return
        with LOCK:
            con = db()
            try:
                ok = banklib.delete_paper(con, int(mpaper.group(1)))
            finally:
                con.close()
        if not ok:
            self._send(404, json.dumps({"error": "试卷不存在"}, ensure_ascii=False), "application/json; charset=utf-8")
            return
        self._send(200, json.dumps({"ok": True}, ensure_ascii=False), "application/json; charset=utf-8")


def serve():
    con = banklib.init_db()
    con.close()
    aivariant.start_worker()
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print("listening on http://%s:%d" % (HOST, PORT), flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if cmd == "import":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 20
        stats = banklib.import_papers(limit)
        print(json.dumps({k: stats[k] for k in ("questions", "with_images", "duplicates_merged", "source_files")}, ensure_ascii=False))
    elif cmd == "serve":
        serve()
    else:
        print("usage: app.py import|serve")
