"""Read Word package structure before splitting into questions.

All source bytes and source objects stay in the caller's private workspace.
IDs depend on the original hash and OOXML location, never on display order.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import shutil
import mimetypes
import posixpath
import re
from pathlib import Path
from zipfile import ZipFile, is_zipfile

from lxml import etree as E
from .numbering import Numbering
from ._source_readers.omml_reader import omml_to_mathml
from ._source_readers.math_codec import mathml_to_latex
from ._source_readers.symbols import decode_symbol, decode_symbol_text

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
M = 'http://schemas.openxmlformats.org/officeDocument/2006/math'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
NS = {'w': W, 'm': M, 'r': R, 'v': 'urn:schemas-microsoft-com:vml',
      'o': 'urn:schemas-microsoft-com:office:office',
      'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
      'wp': 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'}
PARSER = E.XMLParser(resolve_entities=False, no_network=True)
IMPORTER_VERSION = 's1-20261012-cleanup-v6'

def digest(data):
    return hashlib.sha256(data).hexdigest()

def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

def local(node):
    return E.QName(node).localname if isinstance(node.tag, str) else ''

def value(node, path, default=None):
    found = node.find(path, NS)
    return found.get('{' + W + '}val', default) if found is not None else default

def walk_nodes(data):
    if isinstance(data, dict):
        if 'type' in data and 'id' in data:
            yield data
        for val in data.values():
            yield from walk_nodes(val)
    elif isinstance(data, list):
        for val in data:
            yield from walk_nodes(val)

class Reader:
    def __init__(self, archive, directory, source_hash, services):
        self.z, self.directory, self.sha, self.services = archive, directory, source_hash, services
        self.assets, self.asset_manifest, self.records, self.issues, self.compilations = {}, {}, {}, [], {}
        self.tree = None
        self.numbering = Numbering(archive, '{' + W + '}')
        self.rels = {}
        if 'word/_rels/document.xml.rels' in archive.namelist():
            for rel in E.fromstring(archive.read('word/_rels/document.xml.rels'), PARSER):
                target = rel.get('Target', '').replace('\\', '/')
                path = posixpath.normpath(target.lstrip('/') if target.startswith('/') else posixpath.join('word', target))
                self.rels[rel.get('Id')] = (path, rel.get('TargetMode') == 'External')
        self.styles = self.numbering.styles
        self.defaults = []
        if 'word/styles.xml' in archive.namelist():
            root = E.fromstring(archive.read('word/styles.xml'), PARSER)
            default = root.find('w:docDefaults/w:rPrDefault/w:rPr', NS)
            if default is not None:
                self.defaults.append(default)

    def sid(self, kind, locator):
        return kind + '-' + digest((self.sha + ':' + locator).encode())[:24]

    def locator(self, node):
        return 'word/document.xml:' + node.getroottree().getpath(node)

    def record(self, node, kind, **extra):
        locator = self.locator(node)
        ref = self.sid('source', locator)
        self.records.setdefault(ref, {'source_sha256': self.sha, 'locator': locator,
                                     'kind': kind, 'xml': E.tostring(node, encoding='unicode'), **extra})
        return ref

    def issue(self, code, message, node_id=None, severity='review'):
        item = {'id': self.sid('issue', code + str(node_id) + message), 'code': code,
                'severity': severity, 'question_id': None, 'node_id': node_id, 'message': message}
        if not any(x['id'] == item['id'] for x in self.issues):
            self.issues.append(item)
        return item

    def compile(self, latex, source_ref):
        try:
            result = self.services.compile_formula(latex, source_ref=source_ref)
            if not isinstance(result, dict):
                raise ValueError('Compiler must return a result object')
            if result.get('ok') and (not result.get('mathml') or not result.get('omml')):
                raise ValueError('Compiler reported success with empty MathML/OMML')
            return result
        except Exception as exc:
            return {'ok':False, 'latex':latex, 'mathml':None, 'omml':None,
                    'compiler_version':'service-failure', 'diagnostics':[str(exc)]}

    def text(self, node, text, style=None, suffix=''):
        result = {'id': self.sid('text', self.locator(node) + suffix), 'type': 'text', 'text': text}
        if style:
            result['style'] = style
        self.record(node, 'text')
        return result

    def run_style(self, run, paragraph):
        props = list(self.defaults)
        def add_style(sid):
            chain, seen = [], set()
            while sid in self.styles and sid not in seen:
                seen.add(sid)
                node = self.styles[sid]
                child = node.find('{' + W + '}rPr')
                if child is not None:
                    chain.append(child)
                sid = value(node, 'w:basedOn')
            props.extend(reversed(chain))
        add_style(value(paragraph, 'w:pPr/w:pStyle'))
        add_style(value(run, 'w:rPr/w:rStyle'))
        direct = run.find('w:rPr', NS)
        if direct is not None:
            props.append(direct)
        style, fonts = {}, {}
        for prop in props:
            for tag, key in [('b', 'bold'), ('i', 'italic')]:
                el = prop.find('{' + W + '}' + tag)
                if el is not None:
                    style[key] = el.get('{' + W + '}val', '1') not in {'0', 'false', 'off'}
            vertical = value(prop, 'w:vertAlign')
            if vertical:
                style['vertical'] = {'baseline': 'normal'}.get(vertical, vertical)
            f = prop.find('w:rFonts', NS)
            if f is not None:
                fonts.update({E.QName(k).localname: v for k, v in f.attrib.items()})
        return style, fonts

    def formula(self, node, kind='omml', media=None):
        ref = self.record(node, kind, **({'package_part': media} if media else {}))
        result = {'id': self.sid('formula', self.locator(node)), 'type': 'formula',
                  'latex': None, 'status': 'unsupported', 'source_ref': ref}
        try:
            if kind == 'omml':
                mathml = omml_to_mathml(E.tostring(node, encoding='unicode'))
            else:
                from ._source_readers.vendor_mtef import mtef_to_mathml
                mathml = mtef_to_mathml(self.z.read(media))
                if not mathml:
                    raise ValueError('OLE/MTEF reader returned no structure')
            latex = mathml_to_latex(mathml)
            if not latex.strip():
                raise ValueError('Source formula has no expression')
            result.update(latex=latex, status='restored')
            self.records[ref]['mathml'] = mathml
            self.records[ref]['latex'] = latex
            if kind == 'omml':
                self.records[ref]['omml'] = E.tostring(node, encoding='unicode')
            compiled = self.compile(latex, ref)
            self.compilations[result['id']] = compiled
            if not compiled.get('ok') or not compiled.get('mathml') or not compiled.get('omml'):
                result['status'] = 'review_required'
                self.issue('FORMULA_COMPILE_FAILED', '源结构已恢复，编译/Word 往返尚未验证：' + str(compiled.get('diagnostics', [])), result['id'])
        except Exception as exc:
            self.issue('UNSUPPORTED_SOURCE', '原公式已保留，结构恢复失败：' + str(exc), result['id'])
        return result

    def package_target(self, rid, node_id):
        target, external = self.rels.get(rid, ('', False))
        if external or target not in self.z.namelist() or target.startswith('../'):
            self.issue('ASSET_MISSING', '无法读取媒体关系：' + str(rid), node_id, 'error')
            return None
        return target

    def image(self, node, wrapper):
        node_id = self.sid('image', self.locator(node))
        rid = node.get('{' + R + '}embed') or node.get('{' + R + '}id') or node.get('{' + R + '}link')
        part = self.package_target(rid, node_id)
        ref = self.record(wrapper, 'image', relationship_id=rid, package_part=part)
        if part is None:
            return self.text(node, '[缺失图片：' + str(rid) + ']')
        data = self.z.read(part)
        sha = digest(data)
        path = self.directory / 'assets' / (sha + Path(part).suffix.lower())
        path.parent.mkdir(exist_ok=True)
        if not path.exists():
            path.write_bytes(data)
        preview = None
        if Path(part).suffix.lower() in {'.wmf', '.emf', '.tif', '.tiff', '.bmp'}:
            try:
                from PIL import Image
                preview = path.with_suffix('.preview.png')
                with Image.open(io.BytesIO(data)) as img:
                    img.save(preview)
            except Exception:
                preview = None
        asset = self.services.register_asset(str(path), preview_path=str(preview) if preview else None)
        if asset.get('sha256') != sha or not asset.get('id'):
            raise ValueError('Asset service returned wrong source hash or missing ID')
        asset_id = asset['id']
        if asset_id in self.assets and self.assets[asset_id]['sha256'] != sha:
            raise ValueError('Asset identity collision')
        self.assets[asset_id] = asset
        self.asset_manifest[asset_id] = {'asset': asset, 'local_path': str(path),
                                         'preview_path': str(preview) if preview else None, 'package_part': part}
        extent = wrapper.find('.//wp:extent', NS)
        width, height = None, None
        if extent is not None:
            width, height = float(extent.get('cx', '0')) / 36000, float(extent.get('cy', '0')) / 36000
        else:
            shape = wrapper.find('.//v:shape', NS)
            if shape is not None:
                style = dict(re.findall(r'([\w-]+)\s*:\s*([^;]+)', shape.get('style', '')))
                def mm(x):
                    match = re.fullmatch(r'([\d.]+)(pt|in|mm|cm|px)', x or '')
                    return float(match[1]) * {'pt':25.4/72, 'in':25.4, 'mm':1, 'cm':10, 'px':25.4/96}[match[2]] if match else None
                width, height = mm(style.get('width')), mm(style.get('height'))
        if not width or not height:
            try:
                from PIL import Image
                with Image.open(io.BytesIO(data)) as image:
                    width, height = image.width * 25.4 / 96, image.height * 25.4 / 96
            except Exception:
                width, height = 25.4, 25.4
            self.issue('SPLIT_REVIEW', '图片原尺寸未知，暂用像素比例或占位尺寸；需核对。', node_id)
        if wrapper.find('.//wp:anchor', NS) is not None:
            self.issue('SPLIT_REVIEW', '浮动图片按原段落锚点归属，需对照原卷检查视觉位置。', node_id)
        if asset.get('preview_url') is None and preview is None and Path(part).suffix.lower() in {'.wmf', '.emf'}:
            self.issue('UNSUPPORTED_SOURCE', '矢量原图已保存，网页预览需资产服务转换。', node_id)
        self.records[ref]['asset_id'] = asset_id
        return {'id': node_id, 'type': 'image', 'asset_id': asset_id, 'width_mm': width, 'height_mm': height}

    def paragraph(self, paragraph):
        bid = self.sid('paragraph', self.locator(paragraph))
        self.record(paragraph, 'paragraph')
        result = {'id': bid, 'type': 'paragraph', 'align': value(paragraph, 'w:pPr/w:jc', 'left'), 'inlines': []}
        if result['align'] not in {'left', 'center', 'right'}:
            result['align'] = 'left'
        def scan(node, style=None, fonts=None):
            name = local(node)
            if name in {'pPr', 'rPr', 'bookmarkStart', 'bookmarkEnd', 'proofErr', 'fldChar'}:
                return
            if node.tag == '{' + M + '}oMath':
                result['inlines'].append(self.formula(node))
                return
            if name == 'r':
                style, fonts = self.run_style(node, paragraph)
            if name in {'t', 'delText'}:
                text, unknown = decode_symbol_text(node.text or '', fonts or {})
                # Keep exact unknown glyphs, not a guessed replacement square.
                if unknown:
                    text = node.text or ''
                    self.issue('UNMAPPED_SYMBOL', '字体字形未映射，原编码保留：' + str(unknown), bid)
                result['inlines'].append(self.text(node, text, style))
                return
            if name == 'sym':
                code = int(node.get('{' + W + '}char', '0'), 16)
                font = node.get('{' + W + '}font', '')
                text = decode_symbol(code, font)
                if text is None:
                    text = chr(code)
                    self.issue('UNMAPPED_SYMBOL', f'未知字体符号 {font}/{code:04X}，原编码保留。', bid)
                result['inlines'].append(self.text(node, text, style))
                return
            if name in {'tab', 'br', 'cr', 'noBreakHyphen', 'softHyphen'}:
                result['inlines'].append(self.text(node, {'tab':'\t', 'br':'\n', 'cr':'\n', 'noBreakHyphen':'‑', 'softHyphen':'\u00ad'}[name], style))
                return
            if name in {'drawing', 'pict', 'object'}:
                images = node.xpath('.//a:blip | .//v:imagedata', namespaces=NS)
                objects = node.findall('.//o:OLEObject', NS)
                recovered = False
                object_formulas = []
                for obj in objects:
                    part = self.package_target(obj.get('{' + R + '}id'), bid)
                    if part:
                        formula = self.formula(obj, 'ole', part)
                        result['inlines'].append(formula)
                        object_formulas.append(formula)
                        recovered = recovered or formula['latex'] is not None
                for picture in images:
                    image = self.image(picture, node)
                    for formula in object_formulas:
                        self.records[formula['source_ref']].setdefault('fallback_blocks', []).append(
                            {'id':self.sid('paragraph', formula['id'] + '/' + image['id']), 'type':'paragraph',
                             'align':'left', 'inlines':[image]})
                    # Even a restored object's preview is retained in manifest.
                    if not recovered:
                        result['inlines'].append(image)
                        if not objects:
                            self.issue('OCR_REVIEW', '仅有图片来源，保留原图；未自动确认其中的公式。', image['id'])
                if not images and not objects:
                    ref = self.record(node, 'unsupported_drawing')
                    result['inlines'].append(self.text(node, '[待核对绘图对象]'))
                    self.issue('UNSUPPORTED_SOURCE', '非图片绘图已保留于 ' + ref, bid)
                return
            if name in {'instrText', 'fldSimple'}:
                instruction = node.text or node.get('{' + W + '}instr', '')
                self.record(node, 'field_instruction', instruction=instruction)
                self.issue('UNSUPPORTED_SOURCE', '域指令及显示缓存已保留，未解释：' + instruction[:100], bid)
                if name == 'instrText':
                    return
            if name in {'footnoteReference', 'endnoteReference'}:
                ref = self.record(node, 'note_reference')
                result['inlines'].append(self.text(node, '[注' + node.get('{' + W + '}id', '') + ']'))
                self.issue('UNSUPPORTED_SOURCE', '脚注/尾注原文在源包中保留：' + ref, bid)
                return
            if name == 'AlternateContent':
                # Choose one representation; keep both variants in source record.
                self.record(node, 'alternate_content')
                chosen = next((n for n in node if local(n) == 'Choice'), None)
                if chosen is None:
                    chosen = next((n for n in node if local(n) == 'Fallback'), None)
                if chosen is not None:
                    for n in chosen:
                        scan(n, style, fonts)
                return
            if name in {'del', 'ins', 'moveFrom', 'moveTo'}:
                self.record(node, 'tracked_change')
                self.issue('UNSUPPORTED_SOURCE', '文档含修订，保留当前可见文本与原修订；需选择版本。', bid)
            known = {'p', 'r', 'oMathPara', 'hyperlink', 'sdt', 'sdtContent', 'smartTag', 'customXml',
                     'fldSimple', 'del', 'ins', 'moveFrom', 'moveTo', 'sdtPr', 'permStart', 'permEnd'}
            if name not in known and name and not name.endswith('Pr'):
                self.record(node, 'unsupported_inline')
                self.issue('UNSUPPORTED_SOURCE', '未完整解释的行内结构：' + name, bid)
            for child in node:
                scan(child, style, fonts)
        scan(paragraph)
        # Old editors sometimes encode question labels as equation objects.
        # Recognize only an isolated numeric/circled label at a line start;
        # retain its original OLE/OMML in source records for exact provenance.
        line, labelled = '', []
        for inline in result['inlines']:
            if inline['type'] == 'formula' and inline.get('latex'):
                literal = re.sub(r'\\mathrm\{([^{}]*)\}', r'\1', inline['latex'])
                literal = re.sub(r'\s+', '', literal)
                if re.fullmatch(r'[（(]\d{1,2}[）)]|[①②③④⑤⑥⑦⑧⑨⑩]', literal):
                    self.records[inline['source_ref']]['structural_label'] = literal
                    inline = {'id':inline['id'], 'type':'text', 'text':literal}
                else:
                    # Source equations may combine a subquestion label and its
                    # first quantity: (2)20 C or (3)141 g. Split only the explicit
                    # leading parenthesized integer; never synthesize a symbol.
                    leading = re.match(r'^\(\s*(?:\\mathrm\{\s*)?(\d{1,2})(?:\s*\})?\s*\)\s*(.+)$', inline['latex'], re.S)
                    if leading:
                        original_ref = inline['source_ref']
                        label = '(' + leading[1] + ')'
                        labelled.append({'id':self.sid('text',inline['id']+'/label'), 'type':'text', 'text':label})
                        new_ref = self.sid('source', inline['id']+'/remainder')
                        self.records[new_ref] = {'kind':'structural_label_remainder', 'source_sha256':self.sha,
                                                 'locator':self.records[original_ref]['locator'],
                                                 'parent_source_ref':original_ref, 'latex':leading[2], 'label':label}
                        inline = {**inline, 'latex':leading[2], 'source_ref':new_ref, 'status':'review_required'}
                        compiled = self.compile(inline['latex'], new_ref)
                        self.compilations[inline['id']] = compiled
                        self.issue('SPLIT_REVIEW', '源公式含小问标记与数值，已按明确标记分开；原对象完整保留。', inline['id'])
                        if not compiled.get('ok'):
                            self.issue('FORMULA_COMPILE_FAILED', str(compiled.get('diagnostics')), inline['id'])
            labelled.append(inline)
            line += inline['text'] if inline['type']=='text' else '\ufffc'
            line = line.rsplit('\n', 1)[-1]
        result['inlines'] = labelled
        prefix = self.numbering.prefix(paragraph)
        if prefix:
            result['inlines'].insert(0, self.text(paragraph, prefix, suffix='/numbering'))
        self.restore_text_formulas(result)
        return result

    def restore_text_formulas(self, block):
        # Strict chemical grammar; independent volume units remain text.
        from ._source_readers.text_formula import chemical_latex
        nodes = block['inlines']
        output, i, group_index = [], 0, 0
        while i < len(nodes):
            node = nodes[i]
            if node['type'] != 'text':
                output.append(node); i += 1; continue
            end = i
            while end < len(nodes) and nodes[end]['type'] == 'text':
                end += 1
            group = nodes[i:end]
            text = ''.join(n['text'] for n in group)
            # Scan only recognizable chemical tokens or explicit pH comparison.
            pattern = r'pH\s*(?:>=|<=|[><=≥≤])\s*\d+(?:\.\d+)?|(?:[A-Z][a-z]?[\d₀-₉]*|[（(](?:[A-Z][a-z]?[\d₀-₉]*)+[）)][\d₀-₉]*)+(?:\d*[+−-](?![A-Z]))?'
            cursor = 0
            for match in re.finditer(pattern, text):
                start, stop = match.span()
                if start and text[start-1].isascii() and text[start-1].isalpha():
                    continue
                if stop < len(text) and text[stop].isascii() and text[stop].isalpha():
                    continue
                parts = slice_inlines(group, start, stop, self.sid)
                scripts = any(p.get('style', {}).get('vertical') in {'subscript','superscript'} for p in parts)
                if not scripts and not match[0].startswith('pH') and not re.search(r'[（(]|[\d₀-₉]',match[0]):
                    continue
                try:
                    old = [{'kind':'text', 'text': p['text'], **p.get('style', {}),
                            'vert':p.get('style', {}).get('vertical', 'baseline').replace('normal', 'baseline')} for p in parts]
                    latex = chemical_latex(old,allow_placeholders=True)
                except ValueError:
                    continue
                output.extend(slice_inlines(group, cursor, start, self.sid))
                nid = self.sid('formula', block['id'] + f'/text/{group_index}/{start}/{stop}')
                ref = self.sid('source', nid)
                self.records[ref] = {'kind':'styled_text', 'source_sha256':self.sha,
                                     'locator':block['id'] + f':chars[{start}:{stop}]', 'parts':parts}
                output.append({'id':nid, 'type':'formula', 'latex':latex, 'status':'review_required', 'source_ref':ref})
                self.issue('SPLIT_REVIEW', '从文字和脚标恢复的公式边界需核对；原格式保留。', nid)
                compiled = self.compile(latex, ref)
                self.compilations[nid] = compiled
                if not compiled.get('ok'):
                    self.issue('FORMULA_COMPILE_FAILED', str(compiled.get('diagnostics', [])), nid)
                cursor = stop
            output.extend(slice_inlines(group, cursor, len(text), self.sid))
            i = end
            group_index += 1
        block['inlines'] = output

    def table(self, table):
        tid = self.sid('table', self.locator(table))
        self.record(table, 'table')
        cols = [max(float(c.get('{' + W + '}w', '1440')) * 25.4 / 1440, 0.1)
                for c in table.findall('w:tblGrid/w:gridCol', NS)]
        rows, active = [], {}
        for row_index, row in enumerate(table.findall('w:tr', NS)):
            col = int(value(row, 'w:trPr/w:gridBefore', '0'))
            cells, next_active = [], {}
            for cell in row.findall('w:tc', NS):
                span = max(int(value(cell, 'w:tcPr/w:gridSpan', '1')), 1)
                merge = cell.find('w:tcPr/w:vMerge', NS)
                if cell.find('w:tcPr/w:hMerge', NS) is not None:
                    self.issue('UNSUPPORTED_SOURCE', '旧横向 hMerge 布局保留，尚未转换合并属性；需核对原表格。', tid)
                blocks = self.blocks(cell)
                if merge is not None and merge.get('{' + W + '}val', 'continue') == 'continue' and col in active and active[col]['col_span'] == span:
                    origin = active[col]
                    origin['row_span'] += 1
                    next_active[col] = origin
                    if any(block_text(b).strip() or any(n['type']=='image' for n in walk_nodes(b)) for b in blocks):
                        origin['blocks'].extend(blocks)
                        self.issue('SPLIT_REVIEW', '纵向合并的续格含内容，已保留到起始格；需核对布局。', tid)
                else:
                    content = {'id':self.sid('cell', self.locator(cell)), 'col_index':col,
                               'row_span':1, 'col_span':span, 'blocks':blocks}
                    cells.append(content)
                    if merge is not None:
                        next_active[col] = content
                    if merge is not None and merge.get('{' + W + '}val', 'continue') != 'restart':
                        self.issue('SPLIT_REVIEW', '合并续格缺少起始格，保留为独立格。', tid)
                col += span
            while len(cols) < col:
                cols.append(25.4)
            rows.append({'id':self.sid('row', self.locator(row)), 'cells':cells})
            active = next_active
        if not cols:
            cols = [25.4]
        occupied={(rr,cc) for r,row in enumerate(rows) for cell in row['cells']
                  for rr in range(r,r+cell['row_span'])
                  for cc in range(cell['col_index'],cell['col_index']+cell['col_span'])}
        missing=[(r,c) for r in range(len(rows)) for c in range(len(cols)) if (r,c) not in occupied]
        for r,c in missing:
            rows[r]['cells'].append({'id':self.sid('cell',self.locator(table)+f'/omitted-grid/{r}/{c}'),
                                     'col_index':c,'row_span':1,'col_span':1,'blocks':[]})
        if missing:
            self.issue('TABLE_LAYOUT_REVIEW','原 Word 表格省略了网格空位，已保留为空白单元格，请核对原表布局。',tid)
            for row in rows:row['cells'].sort(key=lambda cell:cell['col_index'])
        return {'id':tid, 'type':'table', 'columns_mm':cols, 'rows':rows}

    def blocks(self, parent):
        result = []
        for child in parent:
            name = local(child)
            if name == 'p':
                result.append(self.paragraph(child))
            elif name == 'tbl':
                result.append(self.table(child))
            elif name in {'sdt', 'sdtContent', 'customXml'}:
                result.extend(self.blocks(child))
            elif name not in {'sectPr', 'tcPr', 'sdtPr', 'bookmarkStart', 'bookmarkEnd', 'proofErr', 'permStart', 'permEnd'}:
                ref = self.record(child, 'unsupported_block')
                bid = self.sid('unresolved', self.locator(child))
                fallback = self.blocks(child)
                if not fallback:
                    fallback = [{'id':self.sid('paragraph', bid + '/fallback'), 'type':'paragraph', 'align':'left',
                                 'inlines':[self.text(child, '[待核对原结构：' + name + ']')]}]
                result.append({'id':bid, 'type':'unresolved', 'source_ref':ref,
                               'reason':'未解释的 Word 块：' + name, 'fallback_blocks':fallback})
                self.issue('UNSUPPORTED_SOURCE', '未知块结构保留：' + name, bid)
        return result

def inline_length(node):
    return len(node['text']) if node['type'] == 'text' else 1

def inline_text(nodes):
    return ''.join(n['text'] if n['type']=='text' else '\ufffc' for n in nodes)

def block_text(block):
    if block['type'] == 'paragraph':
        return inline_text(block['inlines'])
    if block['type'] == 'table':
        return '\n'.join(block_text(b) for r in block['rows'] for c in r['cells'] for b in c['blocks'])
    return ''

def slice_inlines(nodes, start, stop, sid):
    result, offset = [], 0
    for node in nodes:
        length = inline_length(node)
        lo, hi = max(start-offset, 0), min(stop-offset, length)
        if hi > lo:
            if lo == 0 and hi == length:
                result.append(copy.deepcopy(node))
            elif node['type'] == 'text':
                part = copy.deepcopy(node)
                part.update(id=sid('text', node['id'] + f':slice[{lo}:{hi}]'), text=node['text'][lo:hi])
                result.append(part)
        offset += length
    return result

def slice_paragraph(block, start, stop, reader):
    return {**block, 'id':reader.sid('paragraph', block['id'] + f':slice[{start}:{stop}]'),
            'inlines':slice_inlines(block['inlines'], start, stop, reader.sid)}

def import_paper(source_path, workspace, services):
    """Return {bundle, report}. Never uses Word or writes outside workspace.

    services: compile_formula and register_asset; optional convert_doc(src, dst).
    DOC conversion is performed on the private copy by an injected converter.
    """
    source = Path(source_path).resolve()
    workspace = Path(workspace).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.suffix.lower() not in {'.doc', '.docx'}:
        raise ValueError('Only DOC/DOCX sources are accepted')
    if source == workspace or workspace in source.parents:
        raise ValueError('Source must be outside this output workspace')
    for method in ('compile_formula', 'register_asset'):
        if not callable(getattr(services, method, None)):
            raise TypeError('services requires ' + method)
    original = source.read_bytes()
    sha = digest(original)
    directory = workspace / sha
    directory.mkdir(parents=True, exist_ok=True)
    result_path = directory / 'import-result.json'
    if result_path.exists():
        existing = json.loads(result_path.read_text(encoding='utf-8'))
        if existing.get('report', {}).get('importer_version') == IMPORTER_VERSION:
            if existing['bundle']['paper']['source_sha256'] != sha:
                raise ValueError('Existing import identity differs; refusing overwrite')
            # A repeat import can populate a new injected asset service without
            # overwriting the immutable import or any edited question version.
            manifest = json.loads(Path(existing['report']['asset_manifest_path']).read_text(encoding='utf-8'))
            for item in manifest.values():
                asset = services.register_asset(item['local_path'], preview_path=item['preview_path'])
                if asset != item['asset']:
                    raise ValueError('Repeat import asset service changed identity; use a new workspace')
            existing['report']['reused'] = True
            existing['report']['source']['unchanged'] = digest(source.read_bytes()) == sha
            return existing
    copy_path = directory / ('original' + source.suffix.lower())
    if not copy_path.exists():
        copy_path.write_bytes(original)
    elif digest(copy_path.read_bytes()) != sha:
        raise ValueError('Existing source copy hash mismatch; refusing overwrite')
    package = copy_path
    preparer=getattr(services,'prepare_package',None)
    if callable(preparer):package=Path(preparer(str(copy_path)) or copy_path)
    if package.resolve()!=copy_path.resolve():
        prepared=directory/'converted.docx'
        if package.resolve()!=prepared.resolve():shutil.copyfile(package,prepared)
        package=prepared
    converter_error = None
    valid_word_package=False
    if is_zipfile(package):
        with ZipFile(package) as candidate_package:
            valid_word_package='word/document.xml' in candidate_package.namelist()
    if not valid_word_package:
        converter = getattr(services, 'convert_doc', None)
        if converter:
            package = directory / 'converted.docx'
            try:
                converter(str(copy_path), str(package))
                if not is_zipfile(package):
                    raise ValueError('DOC converter did not create an OOXML package')
            except Exception as exc:
                converter_error = str(exc)
        else:
            converter_error = 'Legacy DOC requires session-4 injected isolated converter; original retained.'
    if converter_error:
        issue = {'id':'issue-' + sha[:24], 'code':'UNSUPPORTED_SOURCE', 'severity':'error',
                 'question_id':None, 'node_id':None, 'message':converter_error}
        bundle = {'schema_version':1, 'paper':{'id':'paper-' + sha[:24], 'title':source.stem,
                  'question_ids':[], 'source_sha256':sha}, 'questions':[], 'assets':[], 'issues':[issue]}
        report = {'status':'unsupported_source', 'importer_version':IMPORTER_VERSION, 'question_count':0, 'formula_count':0, 'image_count':0,
                  'unsupported_items':[issue], 'source':{'path':str(source), 'sha256':sha, 'unchanged':digest(source.read_bytes())==sha},
                  'original_copy_path':str(copy_path), 'asset_manifest_path':str(directory/'asset-manifest.json'),
                  'user_confirmation':'pending', 'formal_data_written':False}
        write_json(directory/'asset-manifest.json', {})
        result = {'bundle':bundle, 'report':report}
        write_json(directory/'import-result.json', result)
        return result
    with ZipFile(package) as z:
        if 'word/document.xml' not in z.namelist():
            raise ValueError('Not a Word package: word/document.xml is missing')
        root = E.fromstring(z.read('word/document.xml'), PARSER)
        body = root.find('w:body', NS)
        if body is None:
            raise ValueError('Word body missing or namespace unsupported')
        reader = Reader(z, directory, sha, services)
        blocks = reader.blocks(body)
        from .splitter import split_paper
        questions, retained, boundaries = split_paper(blocks, reader)
        from .cleanup import clean_question
        cleanup=[{'question_id':q['id'],**clean_question(q)} for q in questions]
    write_json(directory/'content-cleanup.json',cleanup)
    # Original paragraph IDs may disappear after boundary slicing. Keep source
    # diagnostics visible without dangling references in the public bundle.
    all_ids = {n['id'] for n in walk_nodes(questions)} | {q['id'] for q in questions}
    for issue in reader.issues:
        if issue['node_id'] is not None and issue['node_id'] not in all_ids:
            original_id = issue['node_id']
            owners = [b for b in boundaries if b['source_block_id'] == original_id]
            if len(owners) == 1:
                issue['question_id'] = owners[0]['question_id']
            issue['message'] += ' [原节点 ' + original_id + ']'
            issue['node_id'] = None
    # Attach diagnostics to the owning question, including all nested content.
    for q in questions:
        node_ids = {n['id'] for n in walk_nodes(q)}
        for issue in reader.issues:
            if issue['node_id'] in node_ids or issue['question_id'] == q['id']:
                issue['question_id'] = q['id']
                q['issues'].append(issue)
    bundle = {'schema_version':1, 'paper':{'id':'paper-' + sha[:24], 'title':source.stem,
              'question_ids':[q['id'] for q in questions], 'source_sha256':sha},
              'questions':questions, 'assets':list(reader.assets.values()), 'issues':reader.issues}
    content_nodes = list(walk_nodes(questions))
    report = {'status':'processed_review_required' if reader.issues else 'processed', 'importer_version':IMPORTER_VERSION,
              'question_count':len(questions), 'formula_count':sum(n['type']=='formula' for n in content_nodes),
              'image_count':sum(n['type']=='image' for n in content_nodes),
              'unsupported_items':[x for x in reader.issues if x['code'] in {'UNSUPPORTED_SOURCE','ASSET_MISSING','UNMAPPED_SYMBOL'}],
              'source':{'path':str(source), 'sha256':sha, 'unchanged':digest(source.read_bytes())==sha},
              'original_copy_path':str(copy_path), 'asset_manifest_path':str(directory/'asset-manifest.json'),
              'source_records_path':str(directory/'source-records.json'),
              'ordered_document_path':str(directory/'ordered-document.json'),
              'retained_content_path':str(directory/'retained-content.json'),
              'boundary_report_path':str(directory/'boundaries.json'),
              'compilation_report_path':str(directory/'compilations.json'),
              'user_confirmation':'pending', 'formal_data_written':False}
    if not report['source']['unchanged']:
        raise RuntimeError('Source changed during import')
    write_json(directory/'asset-manifest.json', reader.asset_manifest)
    write_json(directory/'source-records.json', reader.records)
    write_json(directory/'ordered-document.json', blocks)
    write_json(directory/'retained-content.json', retained)
    write_json(directory/'boundaries.json', boundaries)
    write_json(directory/'compilations.json', reader.compilations)
    result = {'bundle':bundle, 'report':report}
    write_json(directory/'import-result.json', result)
    return result
