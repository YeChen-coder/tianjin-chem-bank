"""Explicitly marked migration of legacy strings; no guessing English as math."""
import re
import uuid


def legacy_paragraphs(text):
    """Only $...$, $$...$$, \\( ... \\), \\[ ... \\] are explicit math boundaries."""
    if not isinstance(text, str):
        raise ValueError("旧文本必须是字符串")
    def nid():
        return "legacy-" + uuid.uuid4().hex
    pattern = re.compile(r"\$\$(.+?)\$\$|(?<!\$)\$([^$\n]+)\$(?!\$)|\\\((.+?)\\\)|\\\[(.+?)\\\]", re.S)
    inlines, start = [], 0
    for match in pattern.finditer(text):
        if match.start() > start:
            inlines.append({"id": nid(), "type": "text", "text": text[start:match.start()]})
        inlines.append({"id": nid(), "type": "formula", "latex": next(g for g in match.groups() if g is not None),
                        "status": "review_required", "source_ref": None})
        start = match.end()
    if start < len(text) or not inlines:
        inlines.append({"id": nid(), "type": "text", "text": text[start:]})
    return [{"id": nid(), "type": "paragraph", "align": "left", "inlines": inlines}]
