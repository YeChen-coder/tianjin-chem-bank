// Pure operations on the shared schema v1. Unknown metadata is retained.
export const clone = value => structuredClone(value);
export const newId = () => `editor-${crypto.randomUUID()}`;
export function walk(value, visit, parent = null, key = null) {
  if (!value || typeof value !== 'object') return;
  if (!Array.isArray(value) && value.id) visit(value, parent, key);
  for (const [k, v] of Object.entries(value)) {
    if (v && typeof v === 'object') walk(v, visit, value, k);
  }
}
export function locate(question, id) {
  let found;
  walk(question, (node, parent, key) => { if (node.id === id) found = {node, parent, key}; });
  return found;
}
export function paragraph(text = '') {
  return {id:newId(), type:'paragraph', align:'left', inlines:[{id:newId(), type:'text', text}]};
}
export function targetList(question) {
  const out = [{id:question.id, label:'整道题'}];
  walk({body:question.body,options:question.options,subquestions:question.subquestions}, node => {
    if (node.type === 'blank') out.push({id:node.id, label:`填空 ${node.id.slice(-6)}`});
  });
  // Leaf subquestions need not have a subquestions field.
  const add = list => (list || []).forEach(n => {
    if (!out.some(x => x.id === n.id)) out.push({id:n.id, label:`小问 ${n.label}`});
    add(n.subquestions);
  });
  add(question.subquestions); return out;
}
export function tableGrid(table) {
  const width = table.columns_mm?.length || 0, height = table.rows?.length || 0;
  const grid = Array.from({length:height}, () => Array(width).fill(null));
  if (!width || !height || table.columns_mm.some(w => !Number.isFinite(w) || w <= 0)) throw Error('表格需要有效行、列和正数列宽。');
  table.rows.forEach((row,r) => row.cells.forEach(cell => {
    const {col_index:c, row_span:rs, col_span:cs} = cell;
    if (![c,rs,cs].every(Number.isInteger) || c < 0 || rs < 1 || cs < 1 || r+rs > height || c+cs > width) throw Error('单元格合并范围越界。');
    for (let y=r;y<r+rs;y++) for (let x=c;x<c+cs;x++) {
      if (grid[y][x]) throw Error('表格单元格重叠。'); grid[y][x] = cell;
    }
  }));
  if (grid.some(row => row.some(c => !c))) throw Error('表格存在未覆盖的位置。');
  return grid;
}
export function mergeRight(table, cellId) {
  const grid = tableGrid(table);
  let r = table.rows.findIndex(row => row.cells.some(c => c.id === cellId));
  const cell = table.rows[r]?.cells.find(c => c.id === cellId);
  const right = cell && grid[r][cell.col_index+cell.col_span];
  if (!right || right === cell || right.row_span !== cell.row_span || right.col_index !== cell.col_index+cell.col_span || !table.rows[r].cells.includes(right)) throw Error('只能与同一行、相同高度的右侧单元格合并。');
  cell.col_span += right.col_span; cell.blocks.push(...right.blocks);
  table.rows[r].cells.splice(table.rows[r].cells.indexOf(right),1); tableGrid(table);
}
export function splitCell(table, cellId) {
  tableGrid(table);
  const r = table.rows.findIndex(row => row.cells.some(c => c.id === cellId));
  const cell = table.rows[r]?.cells.find(c => c.id === cellId);
  if (!cell || (cell.col_span === 1 && cell.row_span === 1)) throw Error('这个单元格没有合并。');
  const rs=cell.row_span, cs=cell.col_span;
  cell.row_span=1;cell.col_span=1;
  for(let y=0;y<rs;y++) for(let x=0;x<cs;x++) if(x || y) table.rows[r+y].cells.push({id:newId(), col_index:cell.col_index+x,row_span:1,col_span:1,blocks:[paragraph()]});
  table.rows.forEach(row => row.cells.sort((a,b)=>a.col_index-b.col_index));tableGrid(table);
}
export function mergeDown(table,cellId) {
  const grid=tableGrid(table),r=table.rows.findIndex(row=>row.cells.some(c=>c.id===cellId)),cell=table.rows[r]?.cells.find(c=>c.id===cellId);
  const lower=cell && grid[r+cell.row_span]?.[cell.col_index];
  if(!lower || lower.col_index!==cell.col_index || lower.col_span!==cell.col_span || !table.rows[r+cell.row_span].cells.includes(lower))throw Error('只能与同宽、同列且紧邻下方的单元格合并。');
  table.rows[r+cell.row_span].cells.splice(table.rows[r+cell.row_span].cells.indexOf(lower),1);cell.row_span+=lower.row_span;cell.blocks.push(...lower.blocks);tableGrid(table);
}
export function appendRow(table) {
  tableGrid(table);table.rows.push({id:newId(),cells:table.columns_mm.map((_,c)=>({id:newId(),col_index:c,row_span:1,col_span:1,blocks:[paragraph()]}))});
}
export function appendColumn(table) {
  const grid=tableGrid(table), col=table.columns_mm.length;
  table.columns_mm.push(30);grid.forEach((_,r)=>table.rows[r].cells.push({id:newId(),col_index:col,row_span:1,col_span:1,blocks:[paragraph()]}));
}
export function moveNode(question, nodeId, destinationId, index, asBlock = false) {
  const source=locate(question,nodeId), destination=locate(question,destinationId)?.node;
  if (!source || !Array.isArray(source.parent)) throw Error('找不到原位置。');
  const list = asBlock ? destination?.blocks : destination?.inlines;
  if (!Array.isArray(list)) throw Error('请选择段落或内容区域。');
  if (source.node.type === 'image') {
    const node = source.node;source.parent.splice(Number(source.key),1);
    if(asBlock) node.align ||= 'center'; else delete node.align;
    list.splice(Math.max(0,Math.min(index,list.length)),0,node);
  } else throw Error('仅移动图片节点。');
}
export function validateQuestion(question, assets) {
  const errors=[], ids=new Set(), assetIds=new Set(assets.map(a=>a.id));
  walk(question, node => {
    if(typeof node.id !== 'string' || !node.id || ids.has(node.id)) errors.push(`节点 ID 重复或无效：${node.id}`);ids.add(node.id);
    if(node.type==='table') {try {tableGrid(node);}catch(e){errors.push(e.message);}}
    if(node.type==='image') {
      if(!assetIds.has(node.asset_id)) errors.push('图片资产不存在。');
      if(![node.width_mm,node.height_mm].every(v=>Number.isFinite(v)&&v>0)) errors.push('图片尺寸必须大于零。');
    }
    if(node.type==='formula' && !['restored','review_required','confirmed','unsupported'].includes(node.status)) errors.push('公式状态无效。');
    if(node.type==='blank' && (!Number.isFinite(node.width_mm)||node.width_mm<=0)) errors.push('填空宽度必须大于零。');
  });
  const targets=new Set(targetList(question).map(t=>t.id));
  for(const section of ['answers','explanations']) for(const n of question[section] || []) if(!targets.has(n.target_id)) errors.push('答案或解析的对应位置不存在。');
  return [...new Set(errors)];
}
