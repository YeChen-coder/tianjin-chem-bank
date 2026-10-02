"""DOCX structural reader: retain runs, pictures, tables and native OMML."""
import re
import zipfile
from xml.etree import ElementTree as ET

import question_analysis

QNUMBER = re.compile(r"^\s*(?:第\s*)?(\d{1,3})(?:\s*题\s*[:：.．、]?|\s*[.．、:：)）](?!\d)|(?=\s*[（(]\s*\d+(?:\.\d+)?\s*分))\s*(.*)$", re.S)
ANSWER_SECTION = re.compile(r"^(?:参考答案|答案与解析|参考答案与解析|试题解析|答案解析|答案)(?:\s*[：:]|\s*$)")


class Numbering:
    def __init__(self, archive, W):
        self.W, self.defs, self.nums, self.styles, self.counts = W, {}, {}, {}, {}
        if "word/numbering.xml" in archive.namelist():
            root = ET.fromstring(archive.read("word/numbering.xml"))
            for abstract in root.findall(W + "abstractNum"):
                levels = {}
                for lvl in abstract.findall(W + "lvl"):
                    def value(tag, default):
                        node = lvl.find(W + tag)
                        return node.get(W + "val", default) if node is not None else default
                    levels[int(lvl.get(W + "ilvl", "0"))] = (value("numFmt", "decimal"), value("lvlText", "%1."), int(value("start", "1")))
                self.defs[abstract.get(W + "abstractNumId")] = levels
            for num in root.findall(W + "num"):
                aid = num.find(W + "abstractNumId")
                if aid is not None:
                    levels = dict(self.defs.get(aid.get(W + "val"), {}))
                    for override in num.findall(W + "lvlOverride"):
                        start = override.find(W + "startOverride")
                        ilvl = int(override.get(W + "ilvl", "0"))
                        if start is not None and ilvl in levels:
                            fmt, label, _ = levels[ilvl]
                            levels[ilvl] = fmt, label, int(start.get(W + "val", "1"))
                    self.nums[num.get(W + "numId")] = levels
        if "word/styles.xml" in archive.namelist():
            for style in ET.fromstring(archive.read("word/styles.xml")).findall(W + "style"):
                self.styles[style.get(W + "styleId")] = style

    def prefix(self, paragraph):
        W = self.W
        props = paragraph.find(W + "pPr")
        numpr = props.find(W + "numPr") if props is not None else None
        style = props.find(W + "pStyle") if props is not None else None
        seen = set()
        sid = style.get(W + "val") if style is not None else None
        while numpr is None and sid in self.styles and sid not in seen:
            seen.add(sid)
            node = self.styles[sid]
            numpr = node.find(W + "pPr/" + W + "numPr")
            parent = node.find(W + "basedOn")
            sid = parent.get(W + "val") if parent is not None else None
        if numpr is None:
            return ""
        nid = numpr.find(W + "numId")
        lvl = numpr.find(W + "ilvl")
        if nid is None:
            return ""
        nid, lvl = nid.get(W + "val"), int(lvl.get(W + "val", "0")) if lvl is not None else 0
        definition = self.nums.get(nid, {}).get(lvl)
        if not definition:
            return ""
        fmt, label, start = definition
        if fmt not in {"decimal", "upperLetter", "lowerLetter"}:
            return ""
        key = nid, lvl
        n = self.counts.get(key, start - 1) + 1
        self.counts[key] = n
        for deeper in [k for k in self.counts if k[0] == nid and k[1] > lvl]:
            del self.counts[deeper]
        value = str(n) if fmt == "decimal" else chr(ord("A" if fmt == "upperLetter" else "a") + (n - 1) % 26)
        if fmt == "decimal" and (lvl > 0 or label.startswith("(")):
            return "（" + value + "） "
        return re.sub(r"%\d", value, label) + " "


def parse_docx(path):
    import banklib as b
    questions, warnings, blocks = [], [], []
    with zipfile.ZipFile(path) as archive:
        body = ET.fromstring(archive.read("word/document.xml")).find(b.W + "body")
        if body is None:
            raise RuntimeError("Word 文档没有正文")
        rels, numbering = b.load_rels(archive), Numbering(archive, b.W)
        def para(p):
            parts = b.resolve_parts(b.paragraph_parts(p), archive, rels, warnings)
            prefix = numbering.prefix(p)
            if prefix and not QNUMBER.match(b.para_text(parts)):
                parts.insert(0, {"t": "text", "s": prefix})
            if p.find(".//" + b.W + "object") is not None and not any(x["t"] == "img" for x in parts):
                warnings.append("发现未能读取的嵌入对象，需核对公式或图片")
            return parts
        for block in b.iter_block_items(body):
            if b.local(block.tag) == "p":
                blocks.append(("p", para(block)))
                continue
            # Tables used as layout wrappers can hold whole questions in cells.
            cells = [cell for row in block.findall(b.W + "tr") for cell in row.findall(b.W + "tc")]
            bits = [[para(p) for p in cell.findall(b.W + "p")] for cell in cells]
            starts = [QNUMBER.match(b.para_text(cell[0])) if cell else None for cell in bits]
            question_cells = [m for m in starts if m and len((m.group(2) or "").strip()) >= 6]
            if question_cells and len(question_cells) == sum(bool(any(b.nonempty(p) for p in cell)) for cell in bits):
                for cell in bits:
                    blocks.extend(("p", p) for p in cell)
                if block.find(".//" + b.W + "tbl") is not None:
                    warnings.append("题目布局表格含嵌套表格，请核对内容")
            else:
                rows = [[b.resolve_parts(cell, archive, rels, warnings) for cell in row] for row in b.table_matrix(block)]
                blocks.append(("tbl", {"t": "table", "rows": rows}))
                if block.find(".//" + b.W + "tbl") is not None:
                    warnings.append("嵌套表格已展平，需核对布局")

    section = None
    cur = None
    answer_mode = False
    answer_target = None
    started = False

    def flush():
        nonlocal cur
        if cur is None:
            return
        stem = [p for p in cur["segments"] if b.item_nonempty(p)]
        answers = [p for p in cur["answer_segments"] if b.item_nonempty(p)]
        stem, answers = b.rehome_dumped_choices(stem, answers)
        text = "\n".join(b.item_plain(p) for p in stem).strip()
        info = question_analysis.qtype_info(text, cur["section_type"])
        if info["qtype"] in {"单选题", "多选题"}:
            b.normalize_choice_segments(stem)
            text = "\n".join(b.item_plain(p) for p in stem).strip()
        images = [img for p in stem for img in b.item_imgs(p)]
        if len(re.sub(r"\s", "", text)) >= 6 or images:
            cur.update(body=text, answer="\n".join(b.item_plain(p) for p in answers).strip(),
                       segments=stem, answer_segments=answers, images=images, qtype=info["qtype"],
                       type_suggestion=info, warnings=list(dict.fromkeys(cur["warnings"] + warnings + info["warnings"])))
            questions.append(cur)
        cur = None

    def take(parts):
        nonlocal cur, section, answer_mode, answer_target, started
        text = b.para_text(parts)
        if not b.nonempty(parts):
            return
        if ANSWER_SECTION.match(text.strip()):
            flush()
            answer_mode, answer_target = True, None
            return
        hint = question_analysis.section_type(text)
        if hint or b.SECTION.match(text) or b.QTYPE_HEADER.match(text):
            flush()
            section = hint
            started = True
            return
        if b.SKIP_LINE.match(text):
            return
        numbered = QNUMBER.match(text)
        if answer_mode:
            if numbered:
                matches = [q for q in questions if q["qnum"] == numbered.group(1)]
                answer_target = matches[0] if len(matches) == 1 else None
                if answer_target is None:
                    warnings.append("答案题号无法唯一匹配：" + numbered.group(1))
                    return
                parts = b._slice_parts(parts, numbered.start(2), len(b._raw_para(parts)))
            if answer_target is not None:
                answer_target["answer_segments"].append(parts)
                answer_target["answer"] = "\n".join(b.item_plain(p) for p in answer_target["answer_segments"]).strip()
            return
        if numbered and not b.is_reject(numbered.group(2)):
            # Numeric steps “1. ... 2. ...” inside an existing question are ambiguous.
            if cur and int(numbered.group(1)) <= int(cur.get("qnum") or 0):
                cur["warnings"].append("题干含重复或较小的数字编号，可能为小问，请核对分题")
            else:
                flush()
                started = True
                parts = b._slice_parts(parts, numbered.start(2), len(b._raw_para(parts)))
                cur = {"qnum": numbered.group(1), "segments": [], "answer_segments": [],
                       "section_type": section, "in_answer": False, "warnings": []}
        if cur is None:
            if not started or len(text) < 12:
                return
            cur = {"qnum": "", "segments": [], "answer_segments": [], "section_type": section,
                   "in_answer": False, "warnings": ["未识别到原题号，请核对题目边界"]}
        stem, answer = b.split_answer(parts)
        if answer is not None:
            if b.nonempty(stem):
                cur["segments"].append(stem)
            if b.nonempty(answer):
                cur["answer_segments"].append(answer)
            cur["in_answer"] = True
        elif cur["in_answer"]:
            cur["answer_segments"].append(parts)
        else:
            cur["segments"].append(parts)

    for kind, content in blocks:
        if kind == "tbl":
            if answer_mode:
                warnings.append("答案包含表格，需人工核对题号对应关系")
            elif cur:
                cur["answer_segments" if cur["in_answer"] else "segments"].append(content)
            continue
        for chunk in b.split_glued_paragraph(content, cur):
            take(chunk)
    flush()
    for q in questions:
        q["warnings"] = list(dict.fromkeys(q["warnings"] + warnings))
    return questions
