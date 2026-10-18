"""Derive the original bank's search and classification metadata from v2 content.

These summaries are never a source for editing or Word export. Teacher labels
remain authoritative; automatic suggestions use the original taxonomy rules.
"""
from functools import lru_cache
import json
import unicodedata
from lxml import etree
import banklib
import question_analysis
from .storage import nodes


@lru_cache(maxsize=1024)
def formula_text(latex):
    if not latex:return ''
    try:
        from formula_engine.latex_adapter import convert_latex
        math=etree.fromstring(convert_latex(latex).encode(),etree.XMLParser(resolve_entities=False,no_network=True))
        return unicodedata.normalize('NFKC',''.join(math.itertext()))
    except Exception:
        return latex


def blocks_text(blocks):
    def block_text(block):
        kind=block['type']
        if kind=='paragraph':
            return ''.join(n.get('text','') if n['type']=='text' else formula_text(n.get('latex')) if n['type']=='formula' else '____' if n['type']=='blank' else '' for n in block['inlines'])
        if kind=='table':
            return '\n'.join(' | '.join(blocks_text(c['blocks']) for c in sorted(row['cells'],key=lambda c:c['col_index'])) for row in block['rows'])
        if kind=='unresolved':return blocks_text(block.get('fallback_blocks',[]))
        return ''
    return '\n'.join(block_text(b) for b in blocks)


def question_text(question):
    parts=[blocks_text(question['body'])]
    parts.extend(option['label']+'. '+blocks_text(option['blocks']) for option in question['options'])
    def subs(items):
        for sub in items:
            parts.append(sub['label']+' '+blocks_text(sub['blocks']))
            subs(sub.get('subquestions',[]))
    subs(question['subquestions'])
    # Image-only imports retain the authoritative picture. OCR is searchable
    # provenance, and stops contributing once a teacher writes actual text.
    if not any(n.get('type')=='text' and n.get('text','').strip() for n in nodes({k:question[k] for k in ('body','options','subquestions')})):
        parts.append(question.get('source',{}).get('ocr_search_text',''))
    return '\n'.join(parts)


def sync_metadata(con,lid,question):
    row=con.execute('SELECT category_manual FROM questions WHERE id=?',(lid,)).fetchone()
    if not row:return
    body=question_text(question)
    answer='\n'.join(blocks_text(a['blocks']) for a in question['answers'])
    suggestions=question_analysis.category_suggestions(body,answer,banklib.CATS,banklib.THRESHOLD)
    con.execute('UPDATE questions SET body=?,answer=?,qtype=?,image_count=?,body_manual=? WHERE id=?',
                (body,answer,question['qtype'],sum(n.get('type')=='image' for n in nodes(question)),int(question['origin']=='teacher'),lid))
    if not row['category_manual']:
        major,minor=(suggestions[0]['major'],suggestions[0]['minor']) if suggestions else ('未分类','未分类')
        majors=list(dict.fromkeys(s['major'] for s in suggestions)) or [major]
        minors=list(dict.fromkeys(s['minor'] for s in suggestions)) or [minor]
        for suggestion in suggestions:banklib.ensure_category(con,suggestion['major'],suggestion['minor'])
        con.execute('UPDATE questions SET major=?,minor=? WHERE id=?',(major,minor,lid))
        banklib.set_question_labels(con,lid,majors,minors)
    saved=con.execute('SELECT payload FROM question_metadata WHERE question_id=?',(lid,)).fetchone()
    metadata=json.loads(saved['payload']) if saved else {}
    metadata.update(categories=suggestions,classification_version='legacy-taxonomy-v1',source_summary_revision=question['revision'])
    con.execute('INSERT INTO question_metadata(question_id,payload,needs_review) VALUES (?,?,?) ON CONFLICT(question_id) DO UPDATE SET payload=excluded.payload',
                (lid,json.dumps(metadata,ensure_ascii=False),1))
