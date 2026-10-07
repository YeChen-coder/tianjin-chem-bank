# -*- coding: utf-8 -*-
"""Tianjin grade-9 chemistry question bank: parse, dedup, store, export."""
import base64
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import unicodedata
import zipfile
import tempfile
import posixpath
import shutil
from xml.etree import ElementTree as ET
import question_analysis
import document_display

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.environ.get("CHEM_DATA_DIR") or ROOT)
MIRROR = os.environ.get("CHEM_MIRROR_DIR") or os.path.join(DATA_DIR, "imports")
DB_PATH = os.path.join(DATA_DIR, "bank.sqlite")
MEDIA = os.path.join(DATA_DIR, "media")
MEDIA_ORIG = os.path.join(MEDIA, "original")
IMPORT_DIR = os.path.join(DATA_DIR, "imports")
TAXONOMY_PATH = os.path.join(ROOT, "taxonomy.json")
LO_DIR = os.path.join(DATA_DIR, "converted")

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG = "{http://schemas.openxmlformats.org/package/2006/relationships}"

SUP = {
    "⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4",
    "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9",
    "⁺": "+", "⁻": "-", "⁼": "=", "⁽": "(", "⁾": ")", "ⁿ": "n",
}
SUB_TRANS = str.maketrans("₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₒₓₕₖₗₘₙₚₛₜ", "0123456789+-=()aeoxhklmnpst")

QSTART = re.compile(r"^\s*(\d{1,3})\s*[.．、:：)）](?!\d)\s*(\S.*)?$", re.S)
QSTART_TI = re.compile(r"^第\s*(\d{1,3})\s*题\s*[:：.．、]?\s*(\S.*)$", re.S)
SECTION = re.compile(
    r"^(第\s*[0-9一二三四五六七八九十百]+\s*单元|第\s*[IⅠⅡⅢIII]+\s*卷|[一二三四五六七八九十]+\s*、)"
)
SKIP_LINE = re.compile(
    r"^(注意事项|可能用到的相对原子质量|考生注意|答卷前|祝你考试顺利|姓名|考生号)"
)
REJECT_BITS = (
    "每题选出", "本卷共", "本大题", "可能用到", "答题卡", "答题时",
    "如需改动", "本试卷", "考试结束", "考生须", "用2B", "用２Ｂ",
    "满分100", "满分１００",
)
MARKER_RE = re.compile(r"【[^】\n]{0,16}(?:答案|解析|详解|点睛|分析|解答)】|答案[:：]|解析[:：]")

SKIP_NAME = (
    "计划", "试卷分析", "成绩分析", "分析报告", "答题纸", "道法", "教研",
    "知识点", "知识清单", "必背", "必备", "默写", "实验报告", "答案",
    "总结", "教材分析", "方程式", "高中化学",
)


def local(tag):
    if tag is None:
        return ""
    return tag.split("}")[-1] if "}" in tag else tag


def linearize_unicode(s):
    if not s:
        return ""
    out = []
    i = 0
    n = len(s)
    while i < n:
        if s[i] in SUP:
            j = i + 1
            while j < n and s[j] in SUP:
                j += 1
            out.append("^{" + "".join(SUP[ch] for ch in s[i:j]) + "}")
            i = j
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def omml_text(el):
    tag = local(el.tag)
    if tag == "t":
        return el.text or ""
    if tag.endswith("Pr"):
        return ""
    if tag == "sSub":
        e = sub = ""
        for c in el:
            lt = local(c.tag)
            if lt == "e":
                e = omml_text(c)
            elif lt == "sub":
                sub = omml_text(c)
        return e + sub
    if tag == "sSup":
        e = sup = ""
        for c in el:
            lt = local(c.tag)
            if lt == "e":
                e = omml_text(c)
            elif lt == "sup":
                sup = omml_text(c)
        return e + ("^{" + sup + "}" if sup else "")
    if tag == "sSubSup":
        e = sub = sup = ""
        for c in el:
            lt = local(c.tag)
            if lt == "e":
                e = omml_text(c)
            elif lt == "sub":
                sub = omml_text(c)
            elif lt == "sup":
                sup = omml_text(c)
        return e + sub + ("^{" + sup + "}" if sup else "")
    if tag == "sPre":
        e = sub = sup = ""
        for c in el:
            lt = local(c.tag)
            if lt == "e":
                e = omml_text(c)
            elif lt == "sub":
                sub = omml_text(c)
            elif lt == "sup":
                sup = omml_text(c)
        return (sub or "") + (sup or "") + e
    if tag == "f":
        num = den = ""
        for c in el:
            lt = local(c.tag)
            if lt == "num":
                num = omml_text(c)
            elif lt == "den":
                den = omml_text(c)
        return "(" + num + ")/(" + den + ")"
    if tag == "rad":
        deg = e = ""
        for c in el:
            lt = local(c.tag)
            if lt == "deg":
                deg = omml_text(c)
            elif lt == "e":
                e = omml_text(c)
        if deg.strip():
            return "√[" + deg + "](" + e + ")"
        return "√(" + e + ")"
    if tag == "d":
        beg = end = ""
        pieces = []
        for c in el:
            lt = local(c.tag)
            if lt == "dPr":
                for p in c:
                    plt = local(p.tag)
                    if plt == "begChr":
                        beg = p.get(M + "val") or ""
                    elif plt == "endChr":
                        end = p.get(M + "val") or ""
            elif lt == "e":
                pieces.append(omml_text(c))
        return beg + "".join(pieces) + end
    if tag == "nary":
        ch = sub = sup = e = ""
        for c in el:
            lt = local(c.tag)
            if lt == "naryPr":
                for p in c:
                    if local(p.tag) == "chr":
                        ch = p.get(M + "val") or "∑"
            elif lt == "sub":
                sub = omml_text(c)
            elif lt == "sup":
                sup = omml_text(c)
            elif lt == "e":
                e = omml_text(c)
        return ch + sub + sup + e
    if tag == "m":
        rows = []
        for c in el:
            if local(c.tag) == "mr":
                cells = [omml_text(x) for x in c if local(x.tag) == "e"]
                rows.append(" ".join(cells))
        return " [" + "; ".join(rows) + "] " if rows else ""
    return "".join(omml_text(c) for c in list(el))


def load_taxonomy():
    with open(TAXONOMY_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return data["categories"], int(data.get("threshold", 3))


CATS, THRESHOLD = load_taxonomy()


def classify(text):
    suggestions = question_analysis.category_suggestions(text, "", CATS, THRESHOLD)
    if suggestions:
        return suggestions[0]["major"], suggestions[0]["minor"]
    return "未分类", "未分类"


def question_metadata(con, row):
    saved = con.execute("SELECT payload FROM question_metadata WHERE question_id=?", (row["id"],)).fetchone()
    metadata = json.loads(saved[0]) if saved else {}
    info = question_analysis.qtype_info(row["body"], metadata.get("section_type"))
    # After a manual edit, source section hints may no longer apply.
    if row["body_manual"]:
        info = question_analysis.qtype_info(row["body"])
    warnings = list(metadata.get("warnings") or [])
    if row["qtype_manual"]:
        info = {"qtype": row["qtype"], "reason": "教师已确认", "confidence": "manual", "warnings": []}
        warnings = [w for w in warnings if "题型" not in w and "选项" not in w]
    warnings.extend(info["warnings"])
    if not row["qtype_manual"] and info["qtype"] != row["qtype"]:
        warnings.append("原题库题型与当前建议不同，可手动确认")
    knowledge = question_analysis.knowledge_info(row["body"])
    if not knowledge["points"] and not row["category_manual"]:
        warnings.append("知识点未识别，建议确认")
    metadata.update({"type_suggestion": info, "knowledge": knowledge,
                     "warnings": list(dict.fromkeys(warnings)), "needs_review": bool(warnings)})
    return metadata




QTYPES = ("单选题", "多选题", "填空题", "简答题", "实验题", "计算题")


def _choice_letters_present(text):
    """True when the stem has A–C/A–D options, including an option table.

    A table counts when the first column is A B C D, or one row's cells are
    those letters. Punctuation after the letter (A. / A： / A、) counts too.
    B. C. and D. are still a choice when the first option has no A. label.
    """
    t = text or ""
    opt = r"[.．、:：]"
    has_a = re.search(r"[AＡ]\s*" + opt + r"\s*\S", t)
    has_b = re.search(r"[BＢ]\s*" + opt, t)
    has_c = re.search(r"[CＣ]\s*" + opt, t)
    has_d = re.search(r"[DＤ]\s*" + opt, t)
    if has_a and has_b and has_c:
        return True
    if has_b and has_c and has_d:
        return True
    trans = str.maketrans("ａｂｃｄｅｆＡＢＣＤＥＦabcdef", "ABCDEFABCDEFABCDEF")
    letters = set()
    for line in t.splitlines():
        cells = [c.strip() for c in line.split("|")]
        found = []
        for c in cells:
            m = re.fullmatch(r"([A-Fa-fＡ-Ｆ])\s*[.．、:：]?", c)
            if m:
                found.append(m.group(1).translate(trans))
        if found:
            # first column, or a whole row of option letters
            if found[0] in "ABCDEF":
                letters.add(found[0])
            found_set = set(found)
            if {"A", "B", "C"}.issubset(found_set) or {"B", "C", "D"}.issubset(found_set):
                letters.update(found)
    return {"A", "B", "C"}.issubset(letters) or {"B", "C", "D"}.issubset(letters)


_SUBQ_MARK = re.compile(r"[（(]\s*(\d{1,2})\s*[）)](?!\s*[/／])")
_SUBQ_LINE = re.compile(r"^\s*[（(]\s*(\d{1,2})\s*[）)](?!\s*[/／])")
_CIRCLED_NUMS = "①②③④⑤⑥⑦⑧⑨⑩"
_CIRCLED_MAP = {ch: i + 1 for i, ch in enumerate(_CIRCLED_NUMS)}
_CIRCLED_RE = re.compile("([%s])" % _CIRCLED_NUMS)
_CIRCLED_LINE = re.compile(r"^\s*([%s])" % _CIRCLED_NUMS)
_OPT_LINE_RE = re.compile(r"^\s*[A-FＡ-Ｆ]\s*[.．、:：]")
_ENUM_LINE_RE = re.compile(r"^\s*[A-FＡ-Ｆ]\s*[、,，]\s*[A-FＡ-Ｆ]")
_SUBQ_TRANS = str.maketrans("ＡＢＣＤＥＦ", "ABCDEF")


def _is_option_line(line):
    """An A–F option line, not an 'A、B、C是…' letter list."""
    if not line or _ENUM_LINE_RE.match(line):
        return False
    return _OPT_LINE_RE.match(line) is not None


def _is_option_table_line(line):
    if "|" not in (line or ""):
        return False
    cells = [c.strip() for c in line.split("|")]
    letters = []
    for c in cells:
        m = re.fullmatch(r"([A-FＡ-Ｆ])\s*[.．、:：]?", c)
        if m:
            letters.append(m.group(1).translate(_SUBQ_TRANS))
    found = set(letters)
    if {"A", "B", "C"}.issubset(found) or {"B", "C", "D"}.issubset(found):
        return True
    if cells and re.fullmatch(r"[AＡ]\s*[.．、:：]?", cells[0]):
        return True
    head = line.strip()
    if head.startswith("选项") and re.search(r"[AＡ]", head) and re.search(r"[BＢ]", head):
        return True
    return False


def _option_block_line(lines):
    for i, line in enumerate(lines):
        if _is_option_line(line) or _is_option_table_line(line):
            return i
    return None


def _inline_option_cut(line):
    """Where an inline option label starts, ignoring an A、B、C list."""
    if _ENUM_LINE_RE.match(line or ""):
        return None
    for m in re.finditer(r"[A-DＡ-Ｄ]\s*[.．、:：]", line or ""):
        punct = line[m.end() - 1:m.end()]
        if punct in "、,，" and re.match(r"\s*[A-FＡ-Ｆ]", line[m.end():]):
            continue
        return m.start()
    return None


def _structural_subq_numbers(text):
    """(1)/(2) and circled ①–⑩ markers that structure the stem.

    A marker counts on its own line, or anywhere before the option block.
    Markers that sit only inside an A–D option line do not count. Fraction
    forms such as (1)/(5) are not sub-questions.
    """
    lines = (text or "").splitlines()
    opt_at = _option_block_line(lines)
    nums = set()
    for i, line in enumerate(lines):
        if _is_option_line(line) or _is_option_table_line(line):
            continue
        m = _SUBQ_LINE.match(line)
        if m:
            nums.add(int(m.group(1)))
        else:
            cm = _CIRCLED_LINE.match(line)
            if cm:
                nums.add(_CIRCLED_MAP[cm.group(1)])
        if opt_at is None or i < opt_at:
            cut = _inline_option_cut(line)
            region = line if cut is None else line[:cut]
            for m in _SUBQ_MARK.finditer(region):
                nums.add(int(m.group(1)))
            for m in _CIRCLED_RE.finditer(region):
                nums.add(_CIRCLED_MAP[m.group(1)])
    return nums


def _is_composite_fillin(text):
    """Two or more stem markers plus an underline blank.

    Markers are （1）（2）, (1)(2), and circled ①②③④⑤⑥⑦⑧⑨⑩ when they
    structure the stem (their own line, or before the option block). A later
    sub-part may still contain an A–D block. That item is a 填空题.
    """
    t = text or ""
    if t.count("_") < 2 and t.count("＿") < 2:
        return False
    return len(_structural_subq_numbers(t)) >= 2


def guess_qtype(body, section=None):
    return question_analysis.qtype_info(body, section)["qtype"]




LABEL_MAX = 50



_OPT_LABEL = re.compile(r"(?:^|[\s\n])([A-Fa-fＡ-Ｆ])\s*[.．、:：]")
_OPT_TRANS = str.maketrans("ａｂｃｄｅｆＡＢＣＤＥＦabcdef", "ABCDEFABCDEFABCDEF")
_CHOICE_BLANK = "\uff08  \uff09"  # （ + two U+0020 + ）
_CHOICE_BLANK_RE = re.compile(
    r"[（(][\s_＿﹍▁―—–─]*[A-Da-dＡ-Ｄ]{0,6}[\s_＿﹍▁―—–─]*[）)]"
)
_TRAIL_UNDER = re.compile(r"[_＿﹍▁]{2,}\s*$")
_SECTION_LEAK = re.compile(r"^[一二三四五六七八九十百]+[：:、．.]")


def _option_letters(text):
    out = []
    for m in _OPT_LABEL.finditer(text or ""):
        u = m.group(1).translate(_OPT_TRANS)
        if u not in out:
            out.append(u)
    return out


def _looks_like_choice_block(text):
    """True when text is A–D/A–F options, not an explained answer key."""
    if not text:
        return False
    if re.search(r"【(?:解析|详解|点睛|分析|解答)】|(?:解析|详解)[:：]", text):
        return False
    letters = set(_option_letters(text))
    return {"A", "B", "C"}.issubset(letters) or {"B", "C", "D"}.issubset(letters)


def _leading_answer_key(text):
    """A short letter key before any option label, e.g. 【答案】C then a解析-less quote."""
    raw = text or ""
    m = MARKER_RE.search(raw)
    rest = raw[m.end():] if m and not raw[:m.start()].strip() else raw
    opt = _OPT_LABEL.search(rest)
    head = rest[:opt.start()] if opt else rest
    core = re.sub(r"\s+", "", head)
    return bool(core) and re.fullmatch(r"[A-Fa-fＡ-Ｆ]{1,6}", core) is not None


def _is_section_leak(text):
    t = (text or "").strip()
    if not t or len(t) > 40:
        return False
    return bool(SECTION.match(t) or _SECTION_LEAK.match(t))


def _strip_leading_marker_para(para):
    """Drop a leading 【答案】 marker from a paragraph. True if content remains."""
    if isinstance(para, dict) and para.get("t") == "table":
        return True
    parts = [p for p in para if isinstance(p, dict) and p.get("t") == "text"]
    s = "".join(p.get("s") or "" for p in parts)
    m = MARKER_RE.search(s)
    if m and not s[:m.start()].strip():
        cut = m.end()
        for p in parts:
            cur = p.get("s") or ""
            if cut <= 0:
                break
            if len(cur) <= cut:
                cut -= len(cur)
                p["s"] = ""
            else:
                p["s"] = cur[cut:]
                cut = 0
    remain = "".join((p.get("s") or "") for p in parts).strip()
    if remain:
        return True
    if isinstance(para, dict) and para.get("t") == "table":
        return True
    return any(isinstance(p, dict) and p.get("t") == "img" for p in (para or []))


def rehome_dumped_choices(body_paras, ans_paras):
    """Move A–D/A–F blocks that were stored as the answer back onto the stem.

    Student papers often put 【答案】 on the stem line and the real options in
    the next paragraphs, with no answer key. A short letter key, or an answer
    that already has 解析/详解, stays in the answer.
    """
    if not ans_paras:
        return body_paras, ans_paras
    atext = "\n".join(item_plain(p) for p in ans_paras)
    if not _looks_like_choice_block(atext) or _leading_answer_key(atext):
        return body_paras, ans_paras
    btext = "\n".join(item_plain(p) for p in body_paras)
    if {"A", "B", "C", "D"}.issubset(set(_option_letters(btext))):
        return body_paras, ans_paras
    new_body = list(body_paras)
    kept = []
    for para in ans_paras:
        ptxt = item_plain(para).strip()
        core = MARKER_RE.sub("", ptxt).strip()
        if ptxt and not core:
            kept.append(para)
            continue
        if _is_section_leak(ptxt):
            continue
        if not _strip_leading_marker_para(para):
            continue
        ptxt2 = item_plain(para).strip()
        if _is_section_leak(ptxt2):
            continue
        if ptxt2 or item_imgs(para):
            new_body.append(para)
    return new_body, kept


def _tail_ok(s):
    return re.fullmatch(r"[\s。．.？?！!；;：:、,，]*", s or "") is not None


_OPT_CH = str.maketrans("ＡＢＣＤＥＦａｂｃｄｅｆabcdef", "ABCDEFABCDEFabcdef")
_HWS = " \t\u00a0\u3000"


def _opt_letter(ch):
    if not ch:
        return ""
    return ch.translate(_OPT_CH).upper()


def _first_option_letter(line, col):
    m = re.match(r"\s*([A-Fa-fＡ-Ｆａ-ｆ])", (line or "")[col:])
    if not m:
        return ""
    return _opt_letter(m.group(1))


def _line_ends_with_choice_blank(line):
    """A choice bracket sitting at the end of this line (not mid-sentence)."""
    if not line:
        return False
    core = line.rstrip()
    for m in _CHOICE_BLANK_RE.finditer(core):
        if _tail_ok(core[m.end():]):
            return True
    return False


def _inline_options_cut(line):
    """Where an in-line A–D block starts, or 0 when a bare A leads B. C. D."""
    if not line or _ENUM_LINE_RE.match(line):
        return None
    matches = []
    for m in re.finditer(r"[A-Da-dＡ-Ｄ]\s*[.．、:：]", line):
        punct = line[m.end() - 1:m.end()]
        if punct in "、,，" and re.match(r"\s*[A-Fa-fＡ-Ｆ]", line[m.end():]):
            continue
        matches.append(m)
    if len(matches) < 2:
        return None
    letters = [_opt_letter(line[m.start()]) for m in matches]
    if not ({"A", "B"}.issubset(letters) or {"B", "C"}.issubset(letters) or {"C", "D"}.issubset(letters)):
        return None
    # "A X>Y>Z B. …" or "A    B. C. D." — the block starts at the bare A.
    if re.match(r"^\s*[AＡaａ](?!\s*[.．、:：])", line):
        return 0
    return matches[0].start()


def _find_option_start(text):
    """Index where the A–D block begins (table header included). None if absent.

    When the first labeled option is B/C/D, an unlabeled A option may sit
    after a prompt that already ends with a bracket. The block then starts
    on the line after that bracket, not at B.
    """
    if not text:
        return None
    lines = text.splitlines(keepends=True)
    offsets = []
    pos = 0
    for ln in lines:
        offsets.append(pos)
        pos += len(ln)

    def raw(i):
        return lines[i].rstrip("\r\n")

    hard_i = None
    hard_col = 0
    for i in range(len(lines)):
        r = raw(i)
        if _is_option_table_line(r):
            j = i
            while j > 0 and "|" in raw(j - 1):
                j -= 1
            hard_i, hard_col = j, 0
            break
        if _is_option_line(r):
            hard_i, hard_col = i, 0
            break
        cut = _inline_options_cut(r)
        if cut is not None:
            hard_i, hard_col = i, cut
            break
    if hard_i is None:
        return None
    letter = _first_option_letter(raw(hard_i), hard_col)
    if letter in "BCD":
        blank_line = None
        for i in range(hard_i):
            if _line_ends_with_choice_blank(raw(i)):
                blank_line = i
        if blank_line is not None and blank_line + 1 < len(lines):
            return offsets[blank_line + 1]
    return offsets[hard_i] + hard_col


def _eat_hws(text, a):
    while a > 0 and text[a - 1] in _HWS:
        a -= 1
    return a


def _gap_to_options(s):
    """Whitespace, light punctuation, or empty-table pipes between prompt and options."""
    return re.fullmatch(r"[\s。．.？?！!；;：:、,，|]*", s or "") is not None


def _choice_blank_edits(text):
    """Edits that leave exactly one （  ） at the end of the prompt.

    The bracket sits before options A–D (or the option table), glued to the
    prompt (说法正确的是 / 不正确的是 / …). An existing empty, underscored,
    letter, or English bracket in that spot is replaced. A second bracket
    after D is removed. Edits are (start, end, replacement), later first.
    """
    if not text:
        return []
    opt = _find_option_start(text)
    edits = []
    if opt is None:
        endpos = len(text.rstrip())
        last = None
        for m in _CHOICE_BLANK_RE.finditer(text[:endpos]):
            if _tail_ok(text[m.end():endpos]):
                last = m
                break
        if last is not None:
            edits.append((_eat_hws(text, last.start()), endpos, _CHOICE_BLANK))
        else:
            edits.append((endpos, endpos, _CHOICE_BLANK))
        return edits

    endpos = len(text.rstrip())
    for m in _CHOICE_BLANK_RE.finditer(text[:endpos]):
        if m.start() >= opt and _tail_ok(text[m.end():endpos]):
            edits.append((m.start(), endpos, ""))
            break

    prompt = None
    for m in _CHOICE_BLANK_RE.finditer(text[:opt]):
        if _gap_to_options(text[m.end():opt]):
            prompt = m
    if prompt is not None:
        edits.append((_eat_hws(text, prompt.start()), prompt.end(), _CHOICE_BLANK))
    else:
        insert_at = opt
        while insert_at > 0 and text[insert_at - 1] in _HWS + "\r\n|":
            insert_at -= 1
        edits.append((insert_at, insert_at, _CHOICE_BLANK))
    edits.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return edits


def _segment_pieces(segments):
    pieces = []
    for i, item in enumerate(segments or []):
        if i:
            pieces.append(("sep", "\n"))
        if isinstance(item, dict) and item.get("t") == "table":
            for ri, row in enumerate(item.get("rows") or []):
                if ri:
                    pieces.append(("sep", "\n"))
                for ci, cell in enumerate(row):
                    if ci:
                        pieces.append(("sep", " | "))
                    for p in cell:
                        if isinstance(p, dict) and p.get("t") == "text":
                            pieces.append(("text", p))
        elif isinstance(item, list):
            for p in item:
                if isinstance(p, dict) and p.get("t") == "text":
                    pieces.append(("text", p))
    return pieces


def _apply_span(spans, a, b, repl):
    if a == b:
        ending = inside = starting = None
        for start, end, part in spans:
            if part is None:
                continue
            if start < a and end == a:
                ending = part
            elif start < a < end:
                inside = (part, a - start)
            elif start == a and starting is None:
                starting = part
        if inside is not None:
            part, la = inside
            cur = part.get("s") or ""
            part["s"] = cur[:la] + repl + cur[la:]
            return True
        if ending is not None:
            ending["s"] = (ending.get("s") or "") + repl
            return True
        prev = None
        prev_end = -1
        for start, end, part in spans:
            if part is None or end > a:
                continue
            if end >= prev_end:
                prev, prev_end = part, end
        if prev is not None and (prev.get("s") or "").strip():
            prev["s"] = (prev.get("s") or "") + repl
            return True
        if starting is not None:
            starting["s"] = repl + (starting.get("s") or "")
            return True
        return False
    used = False
    for start, end, part in spans:
        if part is None or end <= a or start >= b:
            continue
        cur = part.get("s") or ""
        la = max(0, a - start)
        lb = min(len(cur), b - start)
        mid = repl if not used else ""
        part["s"] = cur[:la] + mid + cur[lb:]
        used = True
    return used


def _drop_empty_text_tail(segments):
    while segments:
        last = segments[-1]
        if isinstance(last, dict) and last.get("t") == "table":
            return
        if not isinstance(last, list):
            return
        if any(isinstance(p, dict) and p.get("t") == "img" for p in last):
            return
        text = "".join((p.get("s") or "") for p in last if isinstance(p, dict) and p.get("t") == "text")
        if text.strip():
            return
        segments.pop()


def normalize_choice_segments(segments):
    """Put （  ） (U+FF08, two U+0020, U+FF09) at the end of the choice prompt.

    The bracket is inserted right after the stem question, before options
    A–D or an option table — for example 说法正确的是（  ） then A. B. C. D.
    A bracket already there (empty, underscored, a letter, or English
    parentheses) is replaced. A second bracket after option D is removed.
    Do not call this for 填空/简答/实验/计算. A composite fill-in is left
    unchanged, even if one part has an A–D block.
    """
    if segments is None:
        return segments
    plain = "\n".join(item_plain(p) for p in segments)
    if _is_composite_fillin(plain):
        return segments
    pieces = _segment_pieces(segments)
    built = ""
    spans = []
    for kind, val in pieces:
        start = len(built)
        if kind == "sep":
            built += val
            spans.append((start, len(built), None))
        else:
            built += val.get("s") or ""
            spans.append((start, len(built), val))
    for a, b, repl in _choice_blank_edits(built):
        _apply_span(spans, a, b, repl)
    _drop_empty_text_tail(segments)
    return segments


def _dedupe_labels(names):
    out = []
    for name in names or []:
        if name and name not in out:
            out.append(name)
    return out


def load_labels(con, qid, major, minor):
    """Majors and minors for a question. Falls back to the single stored pair."""
    majors, minors = [], []
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "question_majors" in tables:
        majors = [r[0] for r in con.execute(
            "SELECT major FROM question_majors WHERE question_id=? ORDER BY rowid", (qid,)
        )]
    if "question_minors" in tables:
        minors = [r[0] for r in con.execute(
            "SELECT minor FROM question_minors WHERE question_id=? ORDER BY rowid", (qid,)
        )]
    if not majors and major:
        majors = [major]
    if not minors and minor:
        minors = [minor]
    return majors, minors


def set_question_labels(con, qid, majors, minors):
    con.execute("DELETE FROM question_majors WHERE question_id=?", (qid,))
    con.execute("DELETE FROM question_minors WHERE question_id=?", (qid,))
    for name in _dedupe_labels(majors):
        con.execute(
            "INSERT OR IGNORE INTO question_majors (question_id, major) VALUES (?,?)",
            (qid, name),
        )
    for name in _dedupe_labels(minors):
        con.execute(
            "INSERT OR IGNORE INTO question_minors (question_id, minor) VALUES (?,?)",
            (qid, name),
        )


def clean_label(value):
    if not isinstance(value, str):
        return ""
    value = value.replace("\u3000", " ").replace("\xa0", " ")
    value = re.sub(r"[\r\n\t]+", " ", value)
    value = re.sub(r" +", " ", value).strip()
    return value


def ensure_category(con, major, minor):
    con.execute(
        "INSERT OR IGNORE INTO categories (major, minor) VALUES (?,?)",
        (major, minor),
    )


def seed_categories(con):
    """Taxonomy pairs, 未分类, and any pair already stored on a question."""
    for cat in CATS:
        ensure_category(con, cat["major"], cat["minor"])
    ensure_category(con, "未分类", "未分类")
    try:
        rows = con.execute("SELECT DISTINCT major, minor FROM questions").fetchall()
    except sqlite3.OperationalError:
        rows = []
    for major, minor in rows:
        major = clean_label(major or "")
        minor = clean_label(minor or "")
        if major and minor:
            ensure_category(con, major, minor)


def category_tree(con=None):
    """Majors -> minors from the sqlite catalog (not keyword scores)."""
    own = con is None
    if own:
        con = init_db()
    rows = con.execute("SELECT major, minor FROM categories ORDER BY id").fetchall()
    tree = {}
    for major, minor in rows:
        tree.setdefault(major, [])
        if minor not in tree[major]:
            tree[major].append(minor)
    if "未分类" not in tree:
        tree["未分类"] = ["未分类"]
    elif "未分类" not in tree["未分类"]:
        tree["未分类"].append("未分类")
    if own:
        con.close()
    return tree


def snapshot_manual(con):
    """dedup_key -> labels for questions the user classified by hand.

    Values are dicts with major/minor (primary pair) plus majors/minors lists,
    so a reimport keeps every checked class, not only one pair.
    """
    cols = [r[1] for r in con.execute("PRAGMA table_info(questions)")]
    if "category_manual" not in cols:
        return {}
    out = {}
    for qid, key, major, minor in con.execute(
        "SELECT id, dedup_key, major, minor FROM questions WHERE category_manual=1"
    ):
        majors, minors = load_labels(con, qid, major, minor)
        if not majors or not minors:
            continue
        out[key] = {
            "major": majors[0],
            "minor": minors[0],
            "majors": majors,
            "minors": minors,
        }
    return out


def snapshot_qtype(con):
    """dedup_key -> qtype for questions whose 题型 the user saved.

    Same idea as snapshot_manual: a full reimport deletes rows, so the
    hand-set type is copied by dedup_key and written back with qtype_manual=1.
    """
    cols = [r[1] for r in con.execute("PRAGMA table_info(questions)")]
    if "qtype_manual" not in cols:
        return {}
    out = {}
    for key, qtype in con.execute(
        "SELECT dedup_key, qtype FROM questions WHERE qtype_manual=1"
    ):
        if qtype:
            out[key] = qtype
    return out


def snapshot_body(con):
    """dedup_key -> {body, segments} for stems the user edited.

    import_papers deletes every row, then inserts from the papers again.
    The key stays the original paper hash (the edit does not change it),
    so the hand-written stem can be put back with body_manual=1.
    """
    cols = [r[1] for r in con.execute("PRAGMA table_info(questions)")]
    if "body_manual" not in cols:
        return {}
    out = {}
    for key, body, segments in con.execute(
        "SELECT dedup_key, body, segments FROM questions WHERE body_manual=1"
    ):
        if key:
            out[key] = {"body": body or "", "segments": segments or "[]"}
    return out


def _as_label_list(value):
    """Return a cleaned list, or an error string."""
    if not isinstance(value, list):
        return "bad"
    out = []
    for item in value:
        if not isinstance(item, str):
            return "bad"
        name = clean_label(item)
        if not name:
            continue
        if len(name) > LABEL_MAX:
            return "long"
        if name not in out:
            out.append(name)
    return out


def assign_category(con, qid, payload):
    """Lock a question to one or more majors and minors.

    payload majors/minors are the checked names. new_major / new_minor, when
    set, are created in the catalog and added to that question. A legacy
    single major/minor pair is still accepted. Existing single pairs load as
    the one checked item on each side.
    Returns (result, error). error is a short Chinese string, or None.
    """
    row = con.execute("SELECT id FROM questions WHERE id=?", (int(qid),)).fetchone()
    if not row:
        return None, "题目不存在"
    if not isinstance(payload, dict):
        return None, "请求格式不对"

    if "majors" in payload:
        majors = _as_label_list(payload.get("majors"))
    elif isinstance(payload.get("major"), str):
        majors = _as_label_list([payload.get("major")])
    else:
        majors = []
    if "minors" in payload:
        minors = _as_label_list(payload.get("minors"))
    elif isinstance(payload.get("minor"), str):
        minors = _as_label_list([payload.get("minor")])
    else:
        minors = []
    if majors == "bad" or minors == "bad":
        return None, "请求格式不对"
    if majors == "long" or minors == "long":
        return None, "分类名称过长"

    new_major = clean_label(payload.get("new_major") or "")
    new_minor = clean_label(payload.get("new_minor") or "")
    if len(new_major) > LABEL_MAX or len(new_minor) > LABEL_MAX:
        return None, "分类名称过长"
    if new_major and new_major not in majors:
        majors.append(new_major)
    if new_minor and new_minor not in minors:
        minors.append(new_minor)
    if not majors or not minors:
        if new_major and not minors:
            return None, "新建大类时请同时填写小类"
        if new_minor and not majors:
            return None, "请先选择大类，或填写新大类"
        return None, "请选择大类和小类"

    for name in majors:
        if name == new_major:
            continue
        hit = con.execute("SELECT 1 FROM categories WHERE major=?", (name,)).fetchone()
        if not hit:
            return None, "大类不存在，请填写新大类"
    for name in minors:
        if name == new_minor:
            continue
        hit = con.execute("SELECT 1 FROM categories WHERE minor=?", (name,)).fetchone()
        if not hit:
            return None, "该分类不存在，请用新建分类"

    if new_major:
        targets = []
        if new_minor:
            targets.append(new_minor)
        for name in minors:
            if name not in targets:
                targets.append(name)
        if not targets:
            return None, "新建大类时请同时填写小类"
        for name in targets:
            ensure_category(con, new_major, name)
    if new_minor:
        for name in majors:
            ensure_category(con, name, new_minor)

    con.execute(
        "UPDATE questions SET major=?, minor=?, category_manual=1 WHERE id=?",
        (majors[0], minors[0], int(qid)),
    )
    set_question_labels(con, int(qid), majors, minors)
    con.commit()
    return {
        "ok": True,
        "id": int(qid),
        "major": majors[0],
        "minor": minors[0],
        "majors": majors,
        "minors": minors,
        "category_manual": 1,
    }, None



def assign_qtype(con, qid, payload):
    """Save 题型 and lock it (qtype_manual=1) so import will not guess over it.

    Returns (result, error). error is a short Chinese string, or None.
    """
    row = con.execute("SELECT id FROM questions WHERE id=?", (int(qid),)).fetchone()
    if not row:
        return None, "题目不存在"
    if not isinstance(payload, dict):
        return None, "请求格式不对"
    qtype = payload.get("qtype")
    if not isinstance(qtype, str) or qtype not in QTYPES:
        return None, "题型不正确"
    con.execute(
        "UPDATE questions SET qtype=?, qtype_manual=1 WHERE id=?",
        (qtype, int(qid)),
    )
    con.commit()
    return {"ok": True, "id": int(qid), "qtype": qtype, "qtype_manual": 1}, None


def delete_question(con, qid):
    """Delete one question, its sources, paper links, and category labels.

    Papers themselves stay. Returns False when the question does not exist.
    """
    try:
        qid = int(qid)
    except (TypeError, ValueError):
        return False
    if not con.execute("SELECT 1 FROM questions WHERE id=?", (qid,)).fetchone():
        return False
    con.execute("DELETE FROM sources WHERE question_id=?", (qid,))
    con.execute("DELETE FROM paper_items WHERE question_id=?", (qid,))
    con.execute("DELETE FROM question_majors WHERE question_id=?", (qid,))
    con.execute("DELETE FROM question_minors WHERE question_id=?", (qid,))
    con.execute("DELETE FROM question_metadata WHERE question_id=?", (qid,))
    if con.execute("SELECT 1 FROM sqlite_master WHERE name='answer_jobs'").fetchone():
        con.execute("UPDATE answer_jobs SET status='cancelled',phase='cancelled' WHERE question_id=? AND status IN ('queued','running')", (qid,))
        con.execute("DELETE FROM answer_records WHERE question_id=?", (qid,))
    con.execute("DELETE FROM questions WHERE id=?", (qid,))
    con.commit()
    return True


def insert_handwritten(con, payload):
    """Insert one teacher-typed question.

    qtype_manual and category_manual are set. An empty 大类 or 小类 is stored
    as 未分类. An empty 文件来源 stores no sources row. When paper_id is set,
    the new id is appended to that paper without duplicating. Returns
    (result, error). error is Chinese or None.
    """
    if not isinstance(payload, dict):
        return None, "请求格式不对"
    qtype = payload.get("qtype")
    if not isinstance(qtype, str) or qtype not in QTYPES:
        return None, "题型不正确"
    body = payload.get("body")
    if not isinstance(body, str):
        return None, "请填写题干"
    body = body.replace("\r\n", "\n").replace("\r", "\n")
    if not body.strip():
        return None, "请填写题干"
    if len(body) > 20000:
        return None, "题干过长"

    new_major = clean_label(payload.get("new_major") or "")
    new_minor = clean_label(payload.get("new_minor") or "")
    major = clean_label(payload.get("major") or "")
    minor = clean_label(payload.get("minor") or "")
    if (
        len(new_major) > LABEL_MAX or len(new_minor) > LABEL_MAX
        or len(major) > LABEL_MAX or len(minor) > LABEL_MAX
    ):
        return None, "分类名称过长"
    picked_major = bool(new_major or major)
    picked_minor = bool(new_minor or minor)
    if new_major:
        major = new_major
    elif not major:
        major = "未分类"
    if new_minor:
        minor = new_minor
    elif not minor:
        minor = "未分类"
    if picked_major and not new_major:
        if not con.execute("SELECT 1 FROM categories WHERE major=?", (major,)).fetchone():
            return None, "大类不存在，请填写新大类"
    if picked_minor and not new_minor:
        if not con.execute("SELECT 1 FROM categories WHERE minor=?", (minor,)).fetchone():
            return None, "该分类不存在，请用新建分类"

    source = payload.get("source", "")
    if source is None:
        source = ""
    if not isinstance(source, str):
        return None, "文件来源格式不对"
    source = source.strip()
    if len(source) > 500:
        return None, "文件来源过长"

    paper_id = payload.get("paper_id", None)
    attach = paper_id not in (None, "")
    if attach:
        try:
            paper_id = int(paper_id)
        except (TypeError, ValueError):
            return None, "试卷不存在"
        if not con.execute("SELECT 1 FROM papers WHERE id=?", (paper_id,)).fetchone():
            return None, "试卷不存在"

    con.execute("BEGIN")
    try:
        ensure_category(con, major, minor)
        segments = [[{"t": "text", "s": body}]]
        key_raw = norm_key(body, []) + "\nhand\n" + os.urandom(16).hex()
        key = hashlib.sha256(key_raw.encode("utf-8")).hexdigest()
        cur = con.execute(
            """INSERT INTO questions (dedup_key, qnum, body, answer, segments, major, minor, image_count, qtype, category_manual, qtype_manual, body_manual)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                key, "", body, "",
                json.dumps(segments, ensure_ascii=False),
                major, minor, 0, qtype, 1, 1, 1,
            ),
        )
        qid = cur.lastrowid
        set_question_labels(con, qid, [major], [minor])
        if source:
            con.execute(
                "INSERT INTO sources (question_id, rel_path, orig_qnum) VALUES (?,?,?)",
                (qid, source, ""),
            )
        paper = None
        added = 0
        if attach:
            paper, added = append_paper_questions(con, paper_id, [qid])
            if paper is None:
                con.rollback()
                return None, "试卷不存在"
        con.commit()
    except Exception:
        con.rollback()
        raise
    return {
        "ok": True,
        "id": int(qid),
        "qtype": qtype,
        "qtype_manual": 1,
        "category_manual": 1,
        "body_manual": 1,
        "major": major,
        "minor": minor,
        "majors": [major],
        "minors": [minor],
        "body": body,
        "sources": [source] if source else [],
        "paper": paper,
        "added_to_paper": added,
    }, None


def _copy_img(part):
    return dict(part)


def _imgs_in_parts(parts, out):
    for p in parts or []:
        if isinstance(p, dict) and p.get("t") == "img":
            out.append(_copy_img(p))


def _collect_imgs(item, out):
    if isinstance(item, list):
        _imgs_in_parts(item, out)
    elif isinstance(item, dict) and item.get("t") == "table":
        for row in item.get("rows") or []:
            for cell in row:
                _imgs_in_parts(cell, out)


def _set_line(parts, new_text):
    """Replace the text of one paragraph. Image parts stay at the same fraction of the line."""
    parts = parts or []
    raw_len = 0
    img_at = []
    for p in parts:
        if not isinstance(p, dict):
            continue
        if p.get("t") == "img":
            img_at.append((raw_len, _copy_img(p)))
        elif p.get("t") == "text":
            raw_len += len(p.get("s") or "")
    if not img_at:
        return [{"t": "text", "s": new_text}]
    n = len(new_text)
    out = []
    cursor = 0
    for off, img in img_at:
        mapped = int(round(off * n / raw_len)) if raw_len else n
        if mapped < cursor:
            mapped = cursor
        if mapped > n:
            mapped = n
        if mapped > cursor:
            out.append({"t": "text", "s": new_text[cursor:mapped]})
        out.append(img)
        cursor = mapped
    if cursor < n:
        out.append({"t": "text", "s": new_text[cursor:]})
    return out or [{"t": "text", "s": new_text}]


def _apply_table(table, new_lines):
    """Return a list of segments. Keep the grid when the cell layout still matches."""
    rows = table.get("rows") or []
    if rows and len(new_lines) == len(rows):
        new_rows = []
        ok = True
        for row, line in zip(rows, new_lines):
            cells = line.split(" | ")
            if len(cells) != len(row):
                ok = False
                break
            new_rows.append([_set_line(cell, cell_text) for cell, cell_text in zip(row, cells)])
        if ok:
            return [{"t": "table", "rows": new_rows}]
    imgs = []
    _collect_imgs(table, imgs)
    paras = [[{"t": "text", "s": ln}] for ln in new_lines] or [[{"t": "text", "s": ""}]]
    if imgs:
        paras.append(imgs)
    return paras


def sync_stem_segments(segments, new_body):
    """Make segments show new_body. Do not drop image parts.

    When the paragraph/table line grid still matches, images stay inline.
    Otherwise the new text is stored as paragraphs and the images are kept
    in a following paragraph.
    """
    if not isinstance(segments, list):
        segments = []
    if not isinstance(new_body, str):
        new_body = "" if new_body is None else str(new_body)
    old = "\n".join(item_plain(p) for p in segments).strip() if segments else ""
    if segments and new_body == old:
        return segments
    lines = new_body.split("\n")
    blocks = []
    for item in segments:
        plain = item_plain(item)
        blines = plain.split("\n") if plain else [""]
        blocks.append((item, blines))
    if blocks and sum(len(bl) for _, bl in blocks) == len(lines):
        out = []
        i = 0
        for item, blines in blocks:
            chunk = lines[i:i + len(blines)]
            i += len(blines)
            if isinstance(item, list):
                out.append(_set_line(item, "\n".join(chunk)))
            elif isinstance(item, dict) and item.get("t") == "table":
                out.extend(_apply_table(item, chunk))
            else:
                out.append([{"t": "text", "s": "\n".join(chunk)}])
        return out
    imgs = []
    for item in segments:
        _collect_imgs(item, imgs)
    out = [[{"t": "text", "s": ln}] for ln in lines] or [[{"t": "text", "s": ""}]]
    if imgs:
        out.append(imgs)
    return out


def _set_body(con, qid, body):
    """Replace one stem the same way a body edit does. Does not commit.

    Image parts stay. dedup_key is not changed. Returns (result, error).
    """
    row = con.execute(
        "SELECT id, segments FROM questions WHERE id=?", (int(qid),)
    ).fetchone()
    if not row:
        return None, "题目不存在"
    if not isinstance(body, str):
        return None, "请求格式不对"
    segs_raw = row["segments"] if hasattr(row, "keys") else row[1]
    try:
        segments = json.loads(segs_raw or "[]")
    except Exception:
        segments = []
    if not isinstance(segments, list):
        segments = []
    segments = sync_stem_segments(segments, body)
    con.execute(
        "UPDATE questions SET body=?, segments=?, body_manual=1 WHERE id=?",
        (body, json.dumps(segments, ensure_ascii=False), int(qid)),
    )
    return {
        "ok": True,
        "id": int(qid),
        "body": body,
        "segments": segments,
        "body_manual": 1,
    }, None


_PASTE_MAX = 8 * 1024 * 1024


def _decode_pasted_image(data):
    """Store a pasted image/png or image/jpeg data URL. Does not delete files."""
    if not isinstance(data, str) or not data.startswith("data:"):
        return None, "图片格式不对"
    if len(data) > _PASTE_MAX * 2:
        return None, "图片不能超过 8MB"
    header, _, b64 = data.partition(",")
    if not b64 or ";base64" not in header.lower():
        return None, "图片格式不对"
    mime = header[5:].split(";")[0].strip().lower()
    if mime not in ("image/png", "image/jpeg", "image/jpg"):
        return None, "只能粘贴图片"
    try:
        raw = base64.b64decode(b64, validate=False)
    except Exception:
        return None, "图片格式不对"
    if not raw:
        return None, "图片格式不对"
    if len(raw) > _PASTE_MAX:
        return None, "图片不能超过 8MB"
    if mime == "image/png":
        if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
            return None, "只能粘贴图片"
        ext = ".png"
    else:
        if not raw.startswith(b"\xff\xd8"):
            return None, "只能粘贴图片"
        ext = ".jpg"
    return save_image_bytes(raw, ext), None


def _set_body_pieces(con, qid, pieces):
    """Replace the stem with ordered text and image pieces. Does not commit.

    Existing images are kept when the piece names their index or sha.
    New images are data URLs written under media/. Files are not deleted.
    dedup_key stays the original paper hash.
    """
    row = con.execute(
        "SELECT id, segments FROM questions WHERE id=?", (int(qid),)
    ).fetchone()
    if not row:
        return None, "题目不存在"
    if not isinstance(pieces, list):
        return None, "请求格式不对"
    segs_raw = row["segments"] if hasattr(row, "keys") else row[1]
    try:
        old_segments = json.loads(segs_raw or "[]")
    except Exception:
        old_segments = []
    if not isinstance(old_segments, list):
        old_segments = []
    images = _flatten_segment_images(old_segments)
    segs = []
    texts = []
    for piece in pieces:
        if not isinstance(piece, dict):
            return None, "请求格式不对"
        kind = piece.get("t") or piece.get("kind")
        if kind in ("text", "txt"):
            s = piece.get("s", piece.get("text", ""))
            if not isinstance(s, str):
                return None, "请求格式不对"
            s = s.replace("\r\n", "\n").replace("\r", "\n")
            if len(s) > 20000:
                return None, "题干过长"
            if s.strip():
                texts.append(s)
                segs.append([{"t": "text", "s": s}])
        elif kind in ("img", "image"):
            data = piece.get("data")
            if data is None and isinstance(piece.get("src"), str) and piece.get("src", "").startswith("data:"):
                data = piece.get("src")
            if data:
                info, err = _decode_pasted_image(data)
                if err:
                    return None, err
                segs.append([{"t": "img", "sha": info["sha"], "src": info["src"]}])
            else:
                img, _idx = _match_split_image(piece, images)
                if img is None:
                    return None, "图片不存在"
                segs.append([_copy_img(img)])
        else:
            return None, "请求格式不对"
    body = "\n".join(texts)
    if len(body) > 20000:
        return None, "题干过长"
    if not segs:
        segs = [[{"t": "text", "s": ""}]]
    nimg = sum(1 for para in segs if isinstance(para, list) and any(p.get("t") == "img" for p in para))
    con.execute(
        "UPDATE questions SET body=?, segments=?, image_count=?, body_manual=1 WHERE id=?",
        (body, json.dumps(segs, ensure_ascii=False), nimg, int(qid)),
    )
    return {
        "ok": True,
        "id": int(qid),
        "body": body,
        "segments": segs,
        "image_count": nimg,
        "body_manual": 1,
    }, None


def assign_body(con, qid, payload):
    """Save the stem and lock it (body_manual=1).

    A plain body string still updates text and keeps existing images.
    pieces is the full ordered stem: text, existing image indexes or shas,
    and new images as data URLs. dedup_key is left as the original paper hash
    so a later import_papers snapshot can find this row.
    Returns (result, error). error is a short Chinese string, or None.
    """
    if not isinstance(payload, dict):
        return None, "请求格式不对"
    if "pieces" in payload:
        result, err = _set_body_pieces(con, qid, payload.get("pieces"))
    elif isinstance(payload.get("body"), str):
        result, err = _set_body(con, qid, payload["body"])
    else:
        return None, "请求格式不对"
    if err:
        return None, err
    con.commit()
    return result, None


def _flatten_segment_images(segments):
    """Image parts in segment order, including images inside tables."""
    images = []
    def take_parts(parts):
        if not isinstance(parts, list):
            return
        for part in parts:
            if isinstance(part, dict) and part.get("t") == "img":
                images.append(_copy_img(part))
    if not isinstance(segments, list):
        return images
    for item in segments:
        if isinstance(item, list):
            take_parts(item)
        elif isinstance(item, dict) and item.get("t") == "table":
            for row in item.get("rows") or []:
                for cell in row or []:
                    take_parts(cell)
    return images


def _half_pieces(value):
    """One half as ordered pieces: text and image refs. Does not drop order."""
    if isinstance(value, str):
        return [{"t": "text", "s": value}], None
    if isinstance(value, list):
        return value, None
    if isinstance(value, dict):
        if isinstance(value.get("blocks"), list):
            return value["blocks"], None
        if "text" in value or "images" in value:
            text = value.get("text", "")
            images = value.get("images") or []
            if not isinstance(text, str) or not isinstance(images, list):
                return None, "请求格式不对"
            pieces = []
            if text:
                pieces.append({"t": "text", "s": text})
            for ref in images:
                if isinstance(ref, dict):
                    piece = dict(ref)
                    piece["t"] = "img"
                    pieces.append(piece)
                else:
                    pieces.append({"t": "img", "i": ref})
            return pieces, None
    return None, "请求格式不对"


def _match_split_image(piece, images):
    idx = piece.get("i", piece.get("index", None))
    if idx is not None and idx != "":
        try:
            idx = int(idx)
        except (TypeError, ValueError):
            return None, None
        if idx < 0 or idx >= len(images):
            return None, None
        return images[idx], idx
    src = piece.get("src") or ""
    sha = piece.get("sha") or ""
    if not src and not sha:
        return None, None
    for i, img in enumerate(images):
        if sha and img.get("sha") == sha:
            return img, i
        if src and img.get("src") == src:
            return img, i
    return None, None


def _resolve_half(pieces, images):
    """Build segments for one half. Images are copied by reference, never deleted."""
    if not isinstance(pieces, list):
        return None, "请求格式不对"
    segs = []
    texts = []
    shas = []
    indexes = []
    for piece in pieces:
        if not isinstance(piece, dict):
            return None, "请求格式不对"
        kind = piece.get("t") or piece.get("kind")
        if kind in ("text", "txt"):
            s = piece.get("s", piece.get("text", ""))
            if not isinstance(s, str):
                return None, "请求格式不对"
            s = s.replace("\r\n", "\n").replace("\r", "\n")
            if len(s) > 20000:
                return None, "题干过长"
            if s.strip():
                texts.append(s)
                segs.append([{"t": "text", "s": s}])
        elif kind in ("img", "image"):
            img, idx = _match_split_image(piece, images)
            if img is None:
                return None, "图片不存在"
            segs.append([_copy_img(img)])
            shas.append(img.get("sha") or img.get("src") or "")
            if idx is not None:
                indexes.append(idx)
        else:
            return None, "请求格式不对"
    body = "\n".join(texts)
    if len(body) > 20000:
        return None, "题干过长"
    return {
        "segments": segs,
        "body": body,
        "shas": shas,
        "indexes": indexes,
        "has": bool(body.strip() or shas),
    }, None


def split_question(con, qid, payload):
    """Split one question into the original (head) plus one new row (tail).

    Head and tail are ordered text and image pieces. Images move wholly to
    the half that names them and are not deleted from disk. A file used by
    both halves is referenced from both. Tail copies qtype, major/minor, and
    source rows. The new row is manual so a later reimport cannot overwrite
    it. One transaction. When paper_id is set, the new id is appended once.
    Returns (result, error).
    """
    if not isinstance(payload, dict):
        return None, "请求格式不对"
    head_pieces, err = _half_pieces(payload.get("head"))
    if err:
        return None, err
    tail_pieces, err = _half_pieces(payload.get("tail"))
    if err:
        return None, err

    paper_id = payload.get("paper_id", None)
    attach = paper_id not in (None, "")
    if attach:
        try:
            paper_id = int(paper_id)
        except (TypeError, ValueError):
            return None, "试卷不存在"
        if not con.execute("SELECT 1 FROM papers WHERE id=?", (paper_id,)).fetchone():
            return None, "试卷不存在"

    try:
        qid = int(qid)
    except (TypeError, ValueError):
        return None, "题目不存在"
    row = con.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone()
    if not row:
        return None, "题目不存在"

    qtype = row["qtype"] if row["qtype"] else "简答题"
    major = row["major"]
    minor = row["minor"]
    majors, minors = load_labels(con, qid, major, minor)
    sources = con.execute(
        "SELECT rel_path, COALESCE(orig_qnum, '') FROM sources WHERE question_id=? ORDER BY id",
        (qid,),
    ).fetchall()

    try:
        segments = json.loads(row["segments"] or "[]")
    except Exception:
        segments = []
    if not isinstance(segments, list):
        segments = []
    images = _flatten_segment_images(segments)
    # Any original image the client did not name stays on the head. Never drop one.
    named = set()
    for pieces in (head_pieces, tail_pieces):
        for piece in pieces:
            if isinstance(piece, dict) and (piece.get("t") or piece.get("kind")) in ("img", "image"):
                _img, idx = _match_split_image(piece, images)
                if idx is not None:
                    named.add(idx)
    for idx in range(len(images)):
        if idx not in named:
            head_pieces.append({"t": "img", "i": idx})
    head, err = _resolve_half(head_pieces, images)
    if err:
        return None, err
    tail, err = _resolve_half(tail_pieces, images)
    if err:
        return None, err
    if not head["has"] or not tail["has"]:
        return None, "上下两部都要有内容"

    con.execute("BEGIN IMMEDIATE")
    try:
        con.execute(
            "UPDATE questions SET body=?, segments=?, image_count=?, body_manual=1 WHERE id=?",
            (
                head["body"],
                json.dumps(head["segments"], ensure_ascii=False),
                len(head["shas"]),
                qid,
            ),
        )
        tail_segments = tail["segments"]
        key_raw = norm_key(tail["body"], tail["shas"]) + "\nsplit\n" + os.urandom(16).hex()
        key = hashlib.sha256(key_raw.encode("utf-8")).hexdigest()
        cur = con.execute(
            """INSERT INTO questions (dedup_key, qnum, body, answer, segments, major, minor, image_count, qtype, category_manual, qtype_manual, body_manual)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                key, "", tail["body"], "",
                json.dumps(tail_segments, ensure_ascii=False),
                major, minor, len(tail["shas"]), qtype, 1, 1, 1,
            ),
        )
        new_id = int(cur.lastrowid)
        set_question_labels(con, new_id, majors, minors)
        for rel_path, orig_qnum in sources:
            con.execute(
                "INSERT INTO sources (question_id, rel_path, orig_qnum) VALUES (?,?,?)",
                (new_id, rel_path, orig_qnum or ""),
            )
        paper = None
        added = 0
        if attach:
            paper, added = append_paper_questions(con, paper_id, [new_id])
            if paper is None:
                con.rollback()
                return None, "试卷不存在"
        con.commit()
    except Exception:
        con.rollback()
        raise
    question = {
        "id": new_id,
        "qnum": "",
        "body": tail["body"],
        "answer": "",
        "segments": tail_segments,
        "major": major,
        "minor": minor,
        "majors": majors,
        "minors": minors,
        "image_count": len(tail["shas"]),
        "qtype": qtype,
        "qtype_manual": 1,
        "body_manual": 1,
        "category_manual": 1,
        "sources": source_labels(con, new_id),
        "origin": "human",
        "in_bank": 1,
    }
    return {
        "ok": True,
        "id": qid,
        "body": head["body"],
        "segments": head["segments"],
        "image_count": len(head["shas"]),
        "body_manual": 1,
        "new_id": new_id,
        "question": question,
        "paper": paper,
        "added_to_paper": added,
    }, None


def _run_vert(r):
    rpr = None
    for c in r:
        if local(c.tag) == "rPr":
            rpr = c
            break
    if rpr is None:
        return None
    for c in rpr:
        if local(c.tag) == "vertAlign":
            return c.get(W + "val")
    return None


def extract_images(el, parts):
    def rec(node):
        for child in list(node):
            lt = local(child.tag)
            if lt == "AlternateContent":
                choice = fallback = None
                for c in child:
                    cl = local(c.tag)
                    if cl == "Choice":
                        choice = c
                    elif cl == "Fallback":
                        fallback = c
                rec(choice or fallback or child)
                continue
            if lt == "blip":
                rid = child.get(REL + "embed") or child.get(REL + "link")
                if rid:
                    parts.append(("img", rid))
            elif lt == "imagedata":
                rid = child.get(REL + "id")
                if rid:
                    parts.append(("img", rid))
            else:
                rec(child)
    rec(el)


def _run_underlined(r):
    rpr = None
    for c in r:
        if local(c.tag) == "rPr":
            rpr = c
            break
    if rpr is None:
        return False
    for c in rpr:
        if local(c.tag) == "u":
            val = (c.get(W + "val") or "single").lower()
            return val not in ("none", "0", "false", "off")
    return False


def handle_run(r, parts):
    vert = _run_vert(r)
    underlined = _run_underlined(r)
    buf = []

    def flush():
        if not buf:
            return
        s = "".join(buf)
        buf.clear()
        if not s:
            return
        fonts = r.find(W + "rPr/" + W + "rFonts")
        if fonts is not None and "symbol" in " ".join(fonts.attrib.values()).lower():
            s = s.translate(str.maketrans({"\uf03e": ">", "\uf03c": "<", "\uf03d": "=",
                "\uf0b3": "≥", "\uf0a3": "≤", "\uf0ae": "→", "\uf0ad": "↑", "\uf0af": "↓",
                "\uf044": "Δ", "\uf0b0": "°", "\uf0b1": "±", "\uf0b4": "×"}))
        if vert == "superscript":
            s = "^{" + s + "}"
        if underlined:
            s = "".join("__" if ch == "\u3000" else "_" if ch.isspace() and ch not in "\r\n" else ch for ch in s)
        parts.append(("text-sub" if vert == "subscript" else "text", s))

    for child in list(r):
        tag = local(child.tag)
        if tag == "t":
            buf.append(child.text or "")
        elif tag == "tab":
            buf.append("____" if underlined else " ")
        elif tag in ("br", "cr"):
            buf.append("\n")
        elif tag == "sym":
            ch = child.get(W + "char")
            if ch:
                try:
                    buf.append(chr(int(ch, 16)))
                except Exception:
                    pass
        elif tag in ("drawing", "pict", "object"):
            flush()
            extract_images(child, parts)
        elif tag == "AlternateContent":
            flush()
            choice = fallback = None
            for c in child:
                cl = local(c.tag)
                if cl == "Choice":
                    choice = c
                elif cl == "Fallback":
                    fallback = c
            target = choice or fallback
            if target is not None:
                walk_container(target, parts)
        elif tag in ("rPr", "lastRenderedPageBreak", "commentReference"):
            continue
        elif tag == "del":
            continue
        else:
            flush()
            walk_container(child, parts)
    flush()


def walk_container(el, parts):
    for child in list(el):
        tag = local(child.tag)
        if tag in ("pPr", "rPr", "sectPr", "bookmarkStart", "bookmarkEnd", "proofErr",
                   "commentRangeStart", "commentRangeEnd", "del"):
            continue
        if tag.endswith("Pr") and tag not in ("hyperlink",):
            continue
        if tag == "r":
            handle_run(child, parts)
        elif tag in ("oMath", "oMathPara"):
            s = omml_text(child)
            if s:
                parts.append(("math", (s, ET.tostring(child, encoding="unicode"))))
        elif tag == "AlternateContent":
            choice = fallback = None
            for c in child:
                cl = local(c.tag)
                if cl == "Choice":
                    choice = c
                elif cl == "Fallback":
                    fallback = c
            if choice is not None:
                walk_container(choice, parts)
            elif fallback is not None:
                walk_container(fallback, parts)
        elif tag in ("hyperlink", "smartTag", "ins", "sdt", "sdtContent", "fldSimple", "customXml"):
            walk_container(child, parts)
        elif tag in ("drawing", "pict", "object"):
            extract_images(child, parts)
        elif tag in ("bookmarkStart", "bookmarkEnd"):
            continue
        else:
            # unknown wrapper: still look inside, but don't descend into tbl here
            if tag not in ("tbl", "tr", "tc"):
                walk_container(child, parts)


def paragraph_parts(p):
    parts = []
    walk_container(p, parts)
    return parts


def iter_block_items(el):
    for child in list(el):
        tag = local(child.tag)
        if tag == "sdt":
            content = None
            for c in child:
                if local(c.tag) == "sdtContent":
                    content = c
                    break
            if content is not None:
                yield from iter_block_items(content)
        elif tag in ("p", "tbl"):
            yield child


def cell_bits(tc):
    """Return list of part-lists for a cell."""
    bits = []
    for child in list(tc):
        tag = local(child.tag)
        if tag == "p":
            bits.append(paragraph_parts(child))
        elif tag == "tbl":
            for row_parts in table_rows(child):
                bits.append(row_parts)
        elif tag == "sdt":
            for c in child:
                if local(c.tag) == "sdtContent":
                    # treat like a mini body of paragraphs
                    for sub in c:
                        if local(sub.tag) == "p":
                            bits.append(paragraph_parts(sub))
    return bits


def strip_leading_qnum(parts):
    """Remove the original question number, even if it is split across runs."""
    buf = []
    idxs = []
    pat = re.compile(r"^\s*(?:\d{1,3}\s*[.．、:：]|第\s*\d{1,3}\s*题\s*[:：.．、]?)\s*")
    for i, part in enumerate(parts):
        if part.get("t") != "text":
            if buf:
                break
            continue
        buf.append(part.get("s") or "")
        idxs.append(i)
        joined = "".join(buf)
        m = pat.match(joined)
        if m:
            rest = joined[m.end():]
            for j in idxs:
                parts[j]["s"] = ""
            parts[idxs[-1]]["s"] = rest
            return parts
        if len(joined) > 16:
            break
    return parts


def table_matrix(tbl):
    """Rows of cells. Each cell is a raw part list (text/img), not flattened with pipes."""
    rows = []
    for tr in list(tbl):
        if local(tr.tag) != "tr":
            continue
        cells = []
        for tc in list(tr):
            if local(tc.tag) != "tc":
                continue
            parts = []
            for bit in cell_bits(tc):
                if parts and bit:
                    parts.append(("text", "\n"))
                parts.extend(bit)
            cells.append(parts)
        if cells:
            rows.append(cells)
    width = max((len(r) for r in rows), default=0)
    for r in rows:
        while len(r) < width:
            r.append([])
    return rows


def table_rows(tbl):
    rows = []
    for tr in list(tbl):
        if local(tr.tag) != "tr":
            continue
        line = []
        first = True
        for tc in list(tr):
            if local(tc.tag) != "tc":
                continue
            if not first:
                line.append(("text", " | "))
            first = False
            bits = cell_bits(tc)
            for i, parts in enumerate(bits):
                if i:
                    line.append(("text", " "))
                line.extend(parts)
        if line:
            rows.append(line)
    return rows


def load_rels(z):
    rels = {}
    try:
        root = ET.fromstring(z.read("word/_rels/document.xml.rels"))
    except KeyError:
        return rels
    for rel in root:
        rid = rel.get("Id")
        target = rel.get("Target")
        mode = rel.get("TargetMode")
        if not rid or not target or mode == "External":
            continue
        target = target.replace("\\", "/")
        if target.startswith("/"):
            target = target.lstrip("/")
        elif not target.startswith("word/"):
            target = "word/" + target
        rels[rid] = posixpath.normpath(target)
    return rels


def sniff_ext(data, fallback):
    if data.startswith(b"\x89PNG"):
        return ".png"
    if data.startswith(b"\xff\xd8"):
        return ".jpg"
    if data.startswith(b"GIF8"):
        return ".gif"
    if data.startswith(b"BM"):
        return ".bmp"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data.startswith(b"\xd7\xcd\xc6\x9a"):
        return ".wmf"
    if data.startswith(b"\x01\x00\x00\x00") and fallback in (".emf", ".wmf"):
        return fallback
    return fallback or ".bin"


_image_cache = {}


def save_image_bytes(data, ext):
    sha = hashlib.sha256(data).hexdigest()
    cache_key = (os.path.abspath(MEDIA), sha)
    if cache_key in _image_cache:
        return _image_cache[cache_key]
    ext = (ext or "").lower()
    if ext == ".jpeg":
        ext = ".jpg"
    if ext not in (".png", ".jpg", ".gif", ".bmp", ".webp", ".wmf", ".emf", ".tif", ".tiff"):
        ext = sniff_ext(data, ext)
    os.makedirs(MEDIA, exist_ok=True)
    os.makedirs(MEDIA_ORIG, exist_ok=True)
    orig_path = os.path.join(MEDIA_ORIG, sha + ext)
    if not os.path.exists(orig_path):
        with open(orig_path, "wb") as f:
            f.write(data)
    preview = sha + ext
    web_ok = ext in (".png", ".jpg", ".gif", ".bmp", ".webp")
    if not web_ok and ext in (".wmf", ".emf"):
        png_path = os.path.join(MEDIA, sha + ".png")
        if not os.path.exists(png_path) or os.path.getsize(png_path) < 20:
            try:
                if os.name == "nt":
                    command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-File",
                               os.path.join(ROOT, "scripts", "convert_metafile.ps1"),
                               "-InputPath", orig_path, "-OutputPath", png_path]
                else:
                    executable = shutil.which("magick")
                    if not executable:
                        raise RuntimeError("No metafile preview converter")
                    command = [executable, "-density", "144", orig_path, "-background", "white",
                               "-alpha", "remove", "-alpha", "off", "-resize", "1400x1400>", "png:" + png_path]
                subprocess.run(
                    command,
                    check=True, timeout=40,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            except Exception:
                png_path = ""
        if png_path and os.path.exists(png_path) and os.path.getsize(png_path) > 20:
            preview = sha + ".png"
            web_ok = True
    elif web_ok:
        dest = os.path.join(MEDIA, sha + ext)
        if not os.path.exists(dest):
            with open(dest, "wb") as f:
                f.write(data)
        preview = sha + ext
    info = {"sha": sha, "src": "/media/" + preview, "preview": preview, "orig_ext": ext}
    _image_cache[cache_key] = info
    return info


_BLANK_RUN = re.compile(r"[ \t\u3000]{2,}")


def fill_blank_spaces(s):
    """Turn a run of spaces sitting inside a sentence into underscores. Leading and trailing spaces stay."""
    def repl(m):
        if m.start() == 0 or m.end() == len(s):
            return m.group(0)
        return "_" * len(m.group(0))
    return _BLANK_RUN.sub(repl, s)


def resolve_parts(raw_parts, z, rels, warnings=None):
    out = []
    for kind, val in raw_parts:
        if kind in ("text", "text-sub", "math"):
            formula = None
            if kind == "math":
                val, formula = val
            s = fill_blank_spaces(linearize_unicode(val))
            if s:
                part = {"t": "text", "s": s}
                if kind == "text-sub":
                    part["vert"] = "subscript"
                if formula:
                    part["omml"] = formula
                out.append(part)
        elif kind == "img":
            target = rels.get(val)
            if not target or target not in z.namelist():
                if warnings is not None:
                    warnings.append("图片关系缺失或是外部链接，需核对原 Word")
                continue
            low = target.lower()
            if low.endswith(".bin") or "/embeddings/" in low:
                continue
            data = z.read(target)
            if not data:
                continue
            ext = os.path.splitext(target)[1].lower() or sniff_ext(data, "")
            info = save_image_bytes(data, ext)
            out.append({"t": "img", "sha": info["sha"], "src": info["src"], "orig_ext": info["orig_ext"]})
            if warnings is not None and info["preview"].endswith((".wmf", ".emf")):
                warnings.append("矢量图预览不可用，原图保留用于 Word 导出，请核对原卷")
    return out


def para_text(parts):
    return "".join(p["s"] for p in parts if p["t"] == "text").replace("\u3000", " ").strip()


def split_answer(parts):
    body, answer, hit = [], [], False
    for p in parts:
        if hit:
            answer.append(p)
            continue
        if p["t"] != "text":
            body.append(p)
            continue
        s = p["s"]
        m = MARKER_RE.search(s)
        if not m:
            body.append(p)
        else:
            hit = True
            if s[:m.start()].strip():
                body.append(dict(p, s=s[:m.start()]))
            rest = s[m.start():]
            if rest:
                answer.append(dict(p, s=rest))
    if not hit:
        return parts, None
    return body, answer


def is_reject(rest):
    r = rest.strip()
    if "相对原子质量" in r[:24]:
        return True
    for b in REJECT_BITS:
        if r.startswith(b) or b in r[:18]:
            return True
    return False


def match_q(text):
    m = QSTART.match(text) or QSTART_TI.match(text)
    if not m:
        return None
    if is_reject(m.group(2)):
        return None
    if len(m.group(2).strip()) < 2:
        return None
    return m.group(1)


def nonempty(parts):
    return any((p["t"] == "img") or (p["t"] == "text" and p["s"].strip()) for p in parts)


def item_plain(item):
    if isinstance(item, dict) and item.get("t") == "table":
        lines = []
        for row in item.get("rows") or []:
            lines.append(" | ".join(para_text(cell) for cell in row))
        return "\n".join(lines).strip()
    return para_text(item)


def item_imgs(item):
    if isinstance(item, dict) and item.get("t") == "table":
        out = []
        for row in item.get("rows") or []:
            for cell in row:
                out.extend(p["sha"] for p in cell if p.get("t") == "img")
        return out
    return [p["sha"] for p in item if p.get("t") == "img"]


def item_nonempty(item):
    if isinstance(item, dict) and item.get("t") == "table":
        return bool(item_plain(item) or item_imgs(item))
    return nonempty(item)


QTYPE_HEADER = re.compile(
    r"^(?:[一二三四五六七八九十]+\s*[、.．:：]\s*)?"
    r"(?:不定项选择题|单选题|多选题|填空题|简答题|实验题|计算题|选择题)"
    r"\s*(?:[（(][^）)\n]{0,16}[）)])?\s*$"
)
_EMB_Q = re.compile(r"(?:^|\n)[ \t\u3000]*(\d{1,3})\s*[.．、:：)）](?!\d)")
_EMB_SEC = re.compile(
    r"(?:^|\n)[ \t\u3000]*(?:[一二三四五六七八九十]+\s*[、.．:：]\s*)?"
    r"(?:不定项选择题|单选题|多选题|填空题|简答题|实验题|计算题|选择题)"
    r"\s*(?:[（(][^）)\n]{0,16}[）)])?\s*(?=\n|$)"
)


def _slice_parts(parts, a, b):
    out = []
    pos = 0
    for part in parts:
        if part.get("t") != "text":
            if a <= pos < b or (pos == b == len(_raw_para(parts))):
                out.append(part)
            continue
        s = part.get("s") or ""
        start, end = pos, pos + len(s)
        pos = end
        if end <= a or start >= b:
            continue
        la = max(0, a - start)
        lb = min(len(s), b - start)
        piece = s[la:lb]
        if piece:
            copied = dict(part)
            copied["s"] = piece
            out.append(copied)
    return out


def _raw_para(parts):
    return "".join(part.get("s") or "" for part in parts if part.get("t") == "text")


def split_glued_paragraph(parts, cur):
    """Split a paragraph on a later top-level number or a type section header.

    A number after a newline counts when it is at least 10, or greater than the
    question we are already in. (1)(2), circled numbers, and A-D options do not match.
    """
    raw = _raw_para(parts)
    if not raw:
        return [parts]
    cur_n = None
    if cur and str(cur.get("qnum") or "").isdigit():
        cur_n = int(cur["qnum"])
    bounds = {0, len(raw)}
    first_number = QSTART.match(raw) or QSTART_TI.match(raw)
    if first_number:
        cur_n = int(first_number.group(1))
    for m in _EMB_SEC.finditer(raw):
        if m.start() > 0:
            bounds.add(m.start())
    for m in _EMB_Q.finditer(raw):
        if m.start() == 0:
            continue
        qn = int(m.group(1))
        if qn >= 10 or (cur_n is not None and qn > cur_n):
            bounds.add(m.start())
    if len(bounds) <= 2:
        return [parts]
    ordered = sorted(bounds)
    chunks = []
    for i in range(len(ordered) - 1):
        chunk = _slice_parts(parts, ordered[i], ordered[i + 1])
        if para_text(chunk) or any(part.get("t") == "img" for part in chunk):
            chunks.append(chunk)
    return chunks or [parts]

def questions_from_docx(path):
    from docx_import import parse_docx
    return parse_docx(path)




def norm_key(body, images):
    t = unicodedata.normalize("NFKC", body or "")
    t = t.replace("\u3000", " ")
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"^\d{1,3}\s*[.、:.)）]\s*", "", t)
    raw = t + "\n" + "|".join(images)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY,
            dedup_key TEXT NOT NULL UNIQUE,
            qnum TEXT,
            body TEXT NOT NULL,
            answer TEXT,
            segments TEXT NOT NULL,
            major TEXT NOT NULL,
            minor TEXT NOT NULL,
            image_count INTEGER NOT NULL DEFAULT 0,
            qtype TEXT NOT NULL DEFAULT '简答题',
            category_manual INTEGER NOT NULL DEFAULT 0,
            qtype_manual INTEGER NOT NULL DEFAULT 0,
            body_manual INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS sources (
            id INTEGER PRIMARY KEY,
            question_id INTEGER NOT NULL,
            rel_path TEXT NOT NULL,
            orig_qnum TEXT NOT NULL DEFAULT '',
            UNIQUE(question_id, rel_path)
        );
        CREATE TABLE IF NOT EXISTS imports (
            id INTEGER PRIMARY KEY,
            rel_path TEXT UNIQUE,
            question_count INTEGER,
            status TEXT,
            note TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_q_cat ON questions(major, minor);
        """
    )
    cols = [r[1] for r in con.execute("PRAGMA table_info(sources)")]
    if "orig_qnum" not in cols:
        con.execute("ALTER TABLE sources ADD COLUMN orig_qnum TEXT NOT NULL DEFAULT ''")
    qcols = [r[1] for r in con.execute("PRAGMA table_info(questions)")]
    if "qtype" not in qcols:
        con.execute("ALTER TABLE questions ADD COLUMN qtype TEXT NOT NULL DEFAULT '简答题'")
    qcols = [r[1] for r in con.execute("PRAGMA table_info(questions)")]
    if "category_manual" not in qcols:
        con.execute(
            "ALTER TABLE questions ADD COLUMN category_manual INTEGER NOT NULL DEFAULT 0"
        )
    qcols = [r[1] for r in con.execute("PRAGMA table_info(questions)")]
    if "qtype_manual" not in qcols:
        con.execute(
            "ALTER TABLE questions ADD COLUMN qtype_manual INTEGER NOT NULL DEFAULT 0"
        )
    qcols = [r[1] for r in con.execute("PRAGMA table_info(questions)")]
    if "body_manual" not in qcols:
        con.execute(
            "ALTER TABLE questions ADD COLUMN body_manual INTEGER NOT NULL DEFAULT 0"
        )
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY,
            major TEXT NOT NULL,
            minor TEXT NOT NULL,
            UNIQUE(major, minor)
        );
        CREATE TABLE IF NOT EXISTS question_majors (
            question_id INTEGER NOT NULL,
            major TEXT NOT NULL,
            PRIMARY KEY (question_id, major)
        );
        CREATE TABLE IF NOT EXISTS question_minors (
            question_id INTEGER NOT NULL,
            minor TEXT NOT NULL,
            PRIMARY KEY (question_id, minor)
        )
        """
    )
    seed_categories(con)
    con.execute("""CREATE TABLE IF NOT EXISTS question_metadata (
        question_id INTEGER PRIMARY KEY, payload TEXT NOT NULL,
        needs_review INTEGER NOT NULL DEFAULT 0)""")
    ensure_paper_schema(con)
    import aivariant
    aivariant.ensure_schema(con)
    import aianswers
    aianswers.ensure_schema(con)
    ensure_paper_type_order(con)
    con.commit()
    return con


def source_label(rel_path, qnum):
    return document_display.source_info(rel_path, qnum, (DATA_DIR, ROOT, MIRROR))["label"]


def insert_questions(con, rel_path, questions, manual=None, manual_qtype=None, manual_body=None):
    """Returns (new_unique, merged, question_ids).

    question_ids is one id per parsed question, in file order. A duplicate
    that merged onto an existing row still contributes that existing id.

    manual maps dedup_key -> labels captured before a full reimport.
    Those rows are stored again with category_manual=1 so keyword classify
    does not replace a category the user saved. manual_qtype maps dedup_key
    -> qtype when qtype_manual=1; guess_qtype does not overwrite those.
    manual_body maps dedup_key -> {body, segments} when body_manual=1.
    Classification still uses the parsed stem; only the stored stem is the
    hand edit. An existing row is never reclassified here (merge only adds a source).
    """
    manual = manual or {}
    manual_qtype = manual_qtype or {}
    manual_body = manual_body or {}
    new_u = 0
    merged = 0
    ids = []
    for q in questions:
        key = norm_key(q["body"], q["images"])
        suggestions = question_analysis.category_suggestions(q["body"], q["answer"], CATS, THRESHOLD)
        guessed_major, guessed_minor = ((suggestions[0]["major"], suggestions[0]["minor"])
                                       if suggestions else ("未分类", "未分类"))
        if key in manual_qtype:
            qtype = manual_qtype[key]
            qflag = 1
        else:
            qtype = q.get("qtype") or guess_qtype(q["body"], q.get("section_type"))
            qflag = 0
        row = con.execute(
            "SELECT id FROM questions WHERE dedup_key=?", (key,)
        ).fetchone()
        if row:
            qid = row[0]
            merged += 1
        else:
            saved = manual.get(key)
            if saved:
                if isinstance(saved, dict):
                    majors = list(saved.get("majors") or [])
                    minors = list(saved.get("minors") or [])
                    major = saved.get("major") or (majors[0] if majors else guessed_major)
                    minor = saved.get("minor") or (minors[0] if minors else guessed_minor)
                    if major not in majors:
                        majors.insert(0, major)
                    if minor not in minors:
                        minors.insert(0, minor)
                else:
                    major, minor = saved
                    majors, minors = [major], [minor]
                flag = 1
            else:
                major, minor = guessed_major, guessed_minor
                majors = list(dict.fromkeys(c["major"] for c in suggestions)) or [major]
                minors = list(dict.fromkeys(c["minor"] for c in suggestions)) or [minor]
                flag = 0
            ensure_category(con, major, minor)
            for mj in majors:
                ensure_category(con, mj, minor)
            for mn in minors:
                ensure_category(con, major, mn)
            saved_body = manual_body.get(key)
            if isinstance(saved_body, dict) and isinstance(saved_body.get("body"), str):
                store_body = saved_body["body"]
                store_segments = saved_body.get("segments")
                if not isinstance(store_segments, str):
                    store_segments = json.dumps(
                        store_segments if store_segments is not None else q["segments"],
                        ensure_ascii=False,
                    )
                bflag = 1
            else:
                store_body = q["body"]
                store_segments = json.dumps(q["segments"], ensure_ascii=False)
                bflag = 0
            cur = con.execute(
                """INSERT INTO questions (dedup_key, qnum, body, answer, segments, major, minor, image_count, qtype, category_manual, qtype_manual, body_manual)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    key, q.get("qnum") or "", store_body, q.get("answer") or "",
                    store_segments,
                    major, minor, len(q["images"]), qtype, flag, qflag, bflag,
                ),
            )
            qid = cur.lastrowid
            new_u += 1
            set_question_labels(con, qid, majors, minors)
            metadata = {"section_type": q.get("section_type"), "warnings": q.get("warnings") or [],
                        "answer_segments": q.get("answer_segments") or [], "categories": suggestions,
                        "type_suggestion": q.get("type_suggestion") or question_analysis.qtype_info(q["body"]),
                        "knowledge": question_analysis.knowledge_info(q["body"])}
            con.execute("INSERT INTO question_metadata(question_id,payload,needs_review) VALUES(?,?,?)",
                        (qid, json.dumps(metadata, ensure_ascii=False), int(bool(metadata["warnings"])) ))
        con.execute(
            "INSERT OR IGNORE INTO sources (question_id, rel_path, orig_qnum) VALUES (?,?,?)",
            (qid, rel_path, q.get("qnum") or ""),
        )
        ids.append(qid)
    return new_u, merged, ids


def is_paper(name):
    if name.startswith("~$"):
        return False
    for w in SKIP_NAME:
        if w in name:
            return False
    return True


def list_candidates():
    files = []
    for dp, dns, fns in os.walk(MIRROR):
        for fn in fns:
            low = fn.lower()
            if not (low.endswith(".doc") or low.endswith(".docx")):
                continue
            if not is_paper(fn):
                continue
            p = os.path.join(dp, fn)
            try:
                st = os.stat(p)
            except OSError:
                continue
            files.append((st.st_mtime_ns, p))
    files.sort(key=lambda x: (-x[0], x[1]))
    return [p for _, p in files]


def ensure_docx(path):
    if path.lower().endswith(".docx"):
        try:
            with zipfile.ZipFile(path) as z:
                if "word/document.xml" in z.namelist():
                    return path, None
        except zipfile.BadZipFile:
            pass
    os.makedirs(LO_DIR, exist_ok=True)
    with open(path, "rb") as source:
        stamp = hashlib.sha256(source.read()).hexdigest()[:12]
    base = os.path.splitext(os.path.basename(path))[0] + ".docx"
    task_dir = os.path.join(LO_DIR, stamp)
    os.makedirs(task_dir, exist_ok=True)
    out = os.path.join(task_dir, base)
    if os.path.exists(out) and os.path.getsize(out) > 1000:
        return out, None
    executable = shutil.which("soffice") or shutil.which("soffice.exe")
    if not executable:
        raise RuntimeError("此环境没有 LibreOffice，旧版 .doc 请先在 Word 中另存为 .docx")
    from pathlib import Path
    profile = Path(task_dir, "lo-profile").resolve().as_uri()
    r = subprocess.run(
        [executable, "-env:UserInstallation=" + profile, "--headless", "--norestore", "--convert-to", "docx", "--outdir", task_dir, path],
        timeout=180, capture_output=True, text=True,
    )
    if not os.path.exists(out):
        raise RuntimeError("libreoffice failed: " + (r.stderr or r.stdout or "")[-400:])
    return out, None


def import_papers(limit=20):
    """Incremental batch import; retain ids, composed papers and manual edits."""
    con = init_db()
    manual = snapshot_manual(con)
    manual_qtype = snapshot_qtype(con)
    manual_body = snapshot_body(con)
    global _image_cache
    _image_cache = {}
    # rebuild cache from existing media so re-runs don't reconvert if files remain
    # (we still rewrite media as needed)
    failed = []
    imported = []
    total_merged = 0
    for path in list_candidates():
        if len(imported) >= limit:
            break
        rel = os.path.relpath(path, MIRROR).replace("\\", "/")
        try:
            docx, _ = ensure_docx(path)
            qs = questions_from_docx(docx)
            if not qs:
                failed.append({"file": rel, "error": "解析后没有题目"})
                con.execute(
                    "INSERT OR REPLACE INTO imports (rel_path, question_count, status, note) VALUES (?,?,?,?)",
                    (rel, 0, "empty", "no questions"),
                )
                con.commit()
                continue
            con.execute("BEGIN")
            new_u, merged, _ids = insert_questions(
                con, rel, qs, manual=manual, manual_qtype=manual_qtype, manual_body=manual_body
            )
            con.execute(
                "INSERT OR REPLACE INTO imports (rel_path, question_count, status, note) VALUES (?,?,?,?)",
                (rel, len(qs), "ok", f"parsed={len(qs)} new={new_u} merged={merged}"),
            )
            con.commit()
            total_merged += merged
            imported.append({"file": rel, "parsed": len(qs), "new": new_u, "merged": merged})
            print(f"OK {len(imported):02d} parsed={len(qs):4d} new={new_u:4d} merged={merged:3d} {os.path.basename(path)}", flush=True)
        except Exception as e:
            con.rollback()
            failed.append({"file": rel, "error": str(e)})
            print("FAIL", rel, e, flush=True)
            try:
                con.execute(
                    "INSERT OR REPLACE INTO imports (rel_path, question_count, status, note) VALUES (?,?,?,?)",
                    (rel, 0, "error", str(e)[:500]),
                )
                con.commit()
            except Exception:
                pass
    stats = compute_stats(con)
    stats["imported_files"] = imported
    stats["failed"] = failed
    stats["duplicates_merged"] = total_merged
    con.close()
    with open(os.path.join(ROOT, "import-report.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    return stats


def qtype_rank(qtype):
    """Default paper order: 单选, 多选, 填空, 简答, 实验, 计算. Unknown last."""
    try:
        return QTYPES.index(qtype)
    except ValueError:
        return len(QTYPES)


def _qtype_ranks(con, ids):
    ranks = {}
    want = []
    seen = set()
    for raw in ids or []:
        try:
            qid = int(raw)
        except (TypeError, ValueError):
            continue
        if qid not in seen:
            seen.add(qid)
            want.append(qid)
    for i in range(0, len(want), 400):
        chunk = want[i:i + 400]
        if not chunk:
            continue
        marks = ",".join("?" * len(chunk))
        for qid, qtype in con.execute(
            "SELECT id, qtype FROM questions WHERE id IN (%s)" % marks, chunk
        ):
            ranks[qid] = qtype_rank(qtype)
    for qid in want:
        ranks.setdefault(qid, len(QTYPES))
    return ranks


def insert_ids_by_type(con, existing, new_ids):
    """Place new ids by type rank. Does not commit.

    Existing ids stay in their current relative order, including arrow moves.
    A new question is inserted after the last item of the same or an earlier
    type, so a new 填空题 follows existing 填空题 and precedes 简答题.
    New ids of the same type keep the order they were given.
    """
    order = []
    seen = set()
    for qid in list(existing or []) + list(new_ids or []):
        try:
            qid = int(qid)
        except (TypeError, ValueError):
            continue
        if qid in seen:
            continue
        seen.add(qid)
        order.append(qid)
    # Recompute by walking only the new ids onto the kept prefix.
    kept = []
    kept_set = set()
    for qid in existing or []:
        try:
            qid = int(qid)
        except (TypeError, ValueError):
            continue
        if qid not in kept_set:
            kept.append(qid)
            kept_set.add(qid)
    ranks = _qtype_ranks(con, kept + [qid for qid in order if qid not in kept_set])
    result = list(kept)
    for qid in order:
        if qid in kept_set:
            continue
        rank = ranks.get(qid, len(QTYPES))
        insert_at = 0
        for i, eid in enumerate(result):
            if ranks.get(eid, len(QTYPES)) <= rank:
                insert_at = i + 1
        result.insert(insert_at, qid)
        kept_set.add(qid)
    return result


def _write_paper_order(con, paper_id, ordered_ids):
    """Rewrite one paper's items to ordered_ids with positions 0..n-1. No commit."""
    existing = {
        r[0]
        for r in con.execute(
            "SELECT question_id FROM paper_items WHERE paper_id=?", (paper_id,)
        )
    }
    want = []
    seen = set()
    for qid in ordered_ids:
        qid = int(qid)
        if qid in seen:
            continue
        seen.add(qid)
        want.append(qid)
    for qid in existing - seen:
        con.execute(
            "DELETE FROM paper_items WHERE paper_id=? AND question_id=?",
            (paper_id, qid),
        )
    for pos, qid in enumerate(want):
        if qid in existing:
            con.execute(
                "UPDATE paper_items SET position=? WHERE paper_id=? AND question_id=?",
                (pos, paper_id, qid),
            )
        else:
            con.execute(
                "INSERT INTO paper_items (paper_id, question_id, position) VALUES (?,?,?)",
                (paper_id, qid, pos),
            )


def _paper_item_ids(con, paper_id):
    return [
        r[0]
        for r in con.execute(
            "SELECT question_id FROM paper_items WHERE paper_id=? ORDER BY position, question_id",
            (paper_id,),
        )
    ]


def append_paper_questions(con, paper_id, question_ids):
    """Add question ids that are not already on the paper. Does not commit.

    Existing rows and their relative order stay. New ids are inserted by
    type rank (not always at the end). Ids already on the paper are skipped.
    Returns (paper, added_count). paper is None when the paper does not exist.
    """
    try:
        paper_id = int(paper_id)
    except (TypeError, ValueError):
        return None, 0
    if not con.execute("SELECT 1 FROM papers WHERE id=?", (paper_id,)).fetchone():
        return None, 0
    existing = _paper_item_ids(con, paper_id)
    have = set(existing)
    add = [qid for qid in _existing_question_ids(con, question_ids) if qid not in have]
    if add:
        ordered = insert_ids_by_type(con, existing, add)
        _write_paper_order(con, paper_id, ordered)
    con.execute(
        "UPDATE papers SET updated_at=? WHERE id=?",
        (_paper_now(), paper_id),
    )
    return get_paper(con, paper_id), len(add)


def assign_questions_to_papers(con, question_ids, paper_ids):
    """Add questions onto each existing paper. Does not create a paper.

    One transaction per paper. Ids already on a paper are skipped.
    New ids are inserted by type rank. Returns (result, error).
    """
    if not isinstance(question_ids, list) or not isinstance(paper_ids, list):
        return None, "请求格式不对"
    if not question_ids:
        return None, "请至少选择一道题目"
    qids = _existing_question_ids(con, question_ids)
    if not qids:
        return None, "请至少选择一道题目"
    if not paper_ids:
        return None, "请至少选择一套试卷"
    pids = []
    seen = set()
    for raw in paper_ids:
        try:
            pid = int(raw)
        except (TypeError, ValueError):
            return None, "试卷不存在"
        if pid in seen:
            continue
        seen.add(pid)
        pids.append(pid)
    if not pids:
        return None, "请至少选择一套试卷"
    for pid in pids:
        if not con.execute("SELECT 1 FROM papers WHERE id=?", (pid,)).fetchone():
            return None, "试卷不存在"
    results = []
    for pid in pids:
        con.execute("BEGIN IMMEDIATE")
        try:
            paper, added = append_paper_questions(con, pid, qids)
            if paper is None:
                con.rollback()
                return None, "试卷不存在"
            con.commit()
        except Exception:
            con.rollback()
            raise
        results.append({
            "id": paper["id"],
            "name": paper["name"],
            "ids": paper["ids"],
            "added": added,
        })
    return {"ok": True, "paper_count": len(results), "papers": results}, None


def move_paper_question(con, paper_id, question_id, direction):
    """Swap one question with its neighbor. One transaction.

    dir is up or down. The first item stays put on up, the last on down.
    Returns (paper, error). paper includes ids in the saved position order.
    """
    if direction not in ("up", "down"):
        return None, "请求格式不对"
    try:
        paper_id = int(paper_id)
        question_id = int(question_id)
    except (TypeError, ValueError):
        return None, "请求格式不对"
    if not con.execute("SELECT 1 FROM papers WHERE id=?", (paper_id,)).fetchone():
        return None, "试卷不存在"
    ids = _paper_item_ids(con, paper_id)
    if question_id not in ids:
        return None, "题目不在这套试卷"
    index = ids.index(question_id)
    other = index - 1 if direction == "up" else index + 1
    if other < 0 or other >= len(ids):
        return get_paper(con, paper_id), None
    ids[index], ids[other] = ids[other], ids[index]
    con.execute("BEGIN IMMEDIATE")
    try:
        _write_paper_order(con, paper_id, ids)
        con.execute(
            "UPDATE papers SET updated_at=? WHERE id=?",
            (_paper_now(), paper_id),
        )
        con.commit()
    except Exception:
        con.rollback()
        raise
    return get_paper(con, paper_id), None


def ensure_paper_type_order(con):
    """Re-sort every paper by type rank once. Stable within a type.

    Later startups do not run this again, so arrow moves stay saved.
    """
    con.execute(
        "CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    done = con.execute(
        "SELECT 1 FROM app_meta WHERE key='paper_type_order_v1'"
    ).fetchone()
    if done:
        return False
    for (paper_id,) in con.execute("SELECT id FROM papers"):
        ids = _paper_item_ids(con, paper_id)
        ordered = insert_ids_by_type(con, [], ids)
        _write_paper_order(con, paper_id, ordered)
    con.execute(
        "INSERT INTO app_meta (key, value) VALUES ('paper_type_order_v1', '1')"
    )
    con.commit()
    return True


def insert_question_after(con, paper_id, question_id, after_question_id=None):
    """Put question_id on the paper immediately after after_question_id.

    Does not remove the anchor question or any other item. If question_id is
    already on the paper, the order is left as it is. Does not commit in_bank.
    Returns (paper, error).
    """
    try:
        paper_id = int(paper_id)
        question_id = int(question_id)
    except (TypeError, ValueError):
        return None, "请求格式不对"
    if not con.execute("SELECT 1 FROM papers WHERE id=?", (paper_id,)).fetchone():
        return None, "试卷不存在"
    if not con.execute("SELECT 1 FROM questions WHERE id=?", (question_id,)).fetchone():
        return None, "题目不存在"
    already = con.execute(
        "SELECT 1 FROM paper_items WHERE paper_id=? AND question_id=?",
        (paper_id, question_id),
    ).fetchone()
    if not already:
        after_pos = None
        if after_question_id not in (None, ""):
            try:
                after_id = int(after_question_id)
            except (TypeError, ValueError):
                after_id = None
            if after_id is not None and after_id != question_id:
                row = con.execute(
                    "SELECT position FROM paper_items WHERE paper_id=? AND question_id=?",
                    (paper_id, after_id),
                ).fetchone()
                if row:
                    after_pos = row[0]
        if after_pos is None:
            maxpos = con.execute(
                "SELECT COALESCE(MAX(position), -1) FROM paper_items WHERE paper_id=?",
                (paper_id,),
            ).fetchone()[0]
            new_pos = maxpos + 1
        else:
            con.execute(
                "UPDATE paper_items SET position = position + 1 WHERE paper_id=? AND position>?",
                (paper_id, after_pos),
            )
            new_pos = after_pos + 1
        con.execute(
            "INSERT INTO paper_items (paper_id, question_id, position) VALUES (?,?,?)",
            (paper_id, question_id, new_pos),
        )
    con.execute(
        "UPDATE papers SET updated_at=? WHERE id=?",
        (_paper_now(), paper_id),
    )
    con.commit()
    return get_paper(con, paper_id), None


def import_one(path, rel_path, paper_id=None):
    """Import a doc/docx into the bank.

    When paper_id is set, every question from that file (new or merged) is
    also appended onto that paper and the paper is saved in the same commit.
    Ids already on the paper are not duplicated. A missing paper imports nothing.
    """
    con = init_db()
    docx, _ = ensure_docx(path)
    qs = questions_from_docx(docx)
    if not qs:
        con.close()
        raise RuntimeError("没有识别到题目")
    attach = paper_id not in (None, "")
    if attach:
        try:
            paper_id = int(paper_id)
        except (TypeError, ValueError):
            con.close()
            raise LookupError("试卷不存在")
        if not con.execute("SELECT 1 FROM papers WHERE id=?", (paper_id,)).fetchone():
            con.close()
            raise LookupError("试卷不存在")
    con.execute("BEGIN")
    try:
        new_u, merged, ids = insert_questions(con, rel_path, qs)
        paper = None
        added = 0
        if attach:
            paper, added = append_paper_questions(con, paper_id, ids)
            if paper is None:
                raise LookupError("试卷不存在")
        con.execute(
            "INSERT OR REPLACE INTO imports (rel_path, question_count, status, note) VALUES (?,?,?,?)",
            (rel_path, len(qs), "ok", f"parsed={len(qs)} new={new_u} merged={merged}"),
        )
        con.commit()
    except Exception:
        con.rollback()
        con.close()
        raise
    con.close()
    # Stable order, one entry per question from the file (merged ids included).
    seen = set()
    ordered = []
    for qid in ids:
        if qid in seen:
            continue
        seen.add(qid)
        ordered.append(qid)
    reviews = [{"question_id": qid, "qnum": q.get("qnum"), "warnings": q.get("warnings") or []}
               for qid, q in zip(ids, qs) if q.get("warnings")]
    out = {"parsed": len(qs), "new": new_u, "merged": merged, "question_ids": ordered,
           "review_count": len(reviews), "reviews": reviews}
    if paper is not None:
        out["paper"] = paper
        out["added_to_paper"] = added
    return out


def questions_visible_clause():
    """SQL predicate for bank search. Drafts with in_bank=0 stay hidden.

    Do not use this when loading questions by id for a paper or an explicit fetch.
    """
    return "COALESCE(in_bank, 1) = 1"


def compute_stats(con):
    vis = questions_visible_clause()
    n = con.execute("SELECT COUNT(*) FROM questions WHERE " + vis).fetchone()[0]
    with_img = con.execute(
        "SELECT COUNT(*) FROM questions WHERE image_count>0 AND " + vis
    ).fetchone()[0]
    cats = con.execute(
        "SELECT major, minor, COUNT(*) c FROM questions WHERE " + vis + " GROUP BY major, minor ORDER BY c DESC"
    ).fetchall()
    src_files = con.execute("SELECT COUNT(*) FROM imports WHERE status='ok'").fetchone()[0]
    return {
        "questions": n,
        "with_images": with_img,
        "source_files": src_files,
        "categories": [{"major": a, "minor": b, "count": c} for a, b, c in cats],
    }


def open_db():
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def png_size(path):
    try:
        with open(path, "rb") as f:
            sig = f.read(24)
        if sig[:8] == b"\x89PNG\r\n\x1a\n":
            import struct
            w, h = struct.unpack(">II", sig[16:24])
            return w, h
    except Exception:
        pass
    return None


def source_labels(con, qid):
    return [source["label"] for source in source_details(con, qid)]


def source_details(con, qid):
    rows = con.execute(
        "SELECT rel_path, COALESCE(orig_qnum, '') FROM sources WHERE question_id=? ORDER BY id",
        (qid,),
    )
    return [document_display.source_info(a, b, (DATA_DIR, ROOT, MIRROR)) for a, b in rows]


def export_docx(question_ids, dest, keep_source=False, auto_number=True, keep_answers=False):
    from docx import Document
    from docx.shared import Inches, Pt, RGBColor, Cm
    from docx.oxml.ns import qn

    con = open_db()
    doc = Document()
    style = doc.styles["Normal"]
    # 五号 10.5pt. 中文宋体，西文 Times New Roman. Word on Windows applies these names.
    style.font.name = "Times New Roman"
    style.font.size = Pt(10.5)
    style.element.rPr.rFonts.set(qn("w:ascii"), "Times New Roman")
    style.element.rPr.rFonts.set(qn("w:hAnsi"), "Times New Roman")
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
    h = doc.add_paragraph()
    run = h.add_run("化学试卷")
    run.bold = True
    run.font.size = Pt(16)
    run.font.name = "Times New Roman"
    run._element.rPr.rFonts.set(qn("w:ascii"), "Times New Roman")
    run._element.rPr.rFonts.set(qn("w:hAnsi"), "Times New Roman")
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")

    def font_run(run, size=10.5, bold=False, color=None):
        run.bold = bold
        run.font.size = Pt(size)
        run.font.name = "Times New Roman"
        run._element.rPr.rFonts.set(qn("w:ascii"), "Times New Roman")
        run._element.rPr.rFonts.set(qn("w:hAnsi"), "Times New Roman")
        run._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
        if color:
            run.font.color.rgb = RGBColor(*color)

    def write_parts(paragraph, parts):
        for part in document_display.presentation_parts(parts):
            if part.get("t") == "text":
                if part.get("omml"):
                    from docx.oxml import parse_xml
                    paragraph._p.append(parse_xml(part["omml"]))
                    continue
                tokens = r"(\^\{[^{}]+\}|" + document_display.BLANK.pattern + r")"
                for chunk in re.split(tokens, part.get("s") or ""):
                    if not chunk:
                        continue
                    sup = chunk.startswith("^{") and chunk.endswith("}")
                    blank = not sup and document_display.BLANK.fullmatch(chunk)
                    text = "\u00a0" * document_display.blank_length(chunk) if blank else chunk[2:-1] if sup else chunk
                    r = paragraph.add_run(text)
                    font_run(r)
                    r.font.superscript = sup
                    if blank:
                        r.font.underline = True
                    if part.get("vert") == "subscript":
                        r.font.subscript = True
            elif part.get("t") == "img":
                src = part.get("src") or ""
                fn = src.split("/")[-1]
                path = os.path.join(MEDIA, fn)
                sha = part.get("sha") or fn.split(".")[0]
                originals = [os.path.join(MEDIA_ORIG, sha + ext) for ext in (".wmf", ".emf")]
                vector = next((p for p in originals if os.path.isfile(p)), None)
                if vector:
                    from word_export import add_vector
                    if os.path.isfile(path) and path.endswith(".png"):
                        dim = png_size(path)
                    else:
                        dim = None
                    width = Inches(min(5.2, max(0.35, dim[0] / 144.0))) if dim else Inches(4.2)
                    picture = add_vector(paragraph, vector, width)
                    if dim and dim[0]:
                        picture.height = int(width * dim[1] / dim[0])
                    continue
                if os.path.exists(path):
                    dim = png_size(path)
                    width = Inches(4.2)
                    if dim:
                        width = Inches(min(5.2, max(0.35, dim[0] / 144.0)))
                    try:
                        paragraph.add_run().add_picture(path, width=width)
                    except Exception as exc:
                        con.close()
                        raise RuntimeError("图片无法写入 Word，请先修复或转换图片：" + fn) from exc
                else:
                    con.close()
                    raise RuntimeError("题目图片缺失，已停止导出：" + fn)

    def write_table(rows):
        cols = max((len(r) for r in rows), default=0)
        if cols < 1:
            return
        table = doc.add_table(rows=len(rows), cols=cols)
        table.style = 'Table Grid'
        table.autofit = True
        for ri, row_cells in enumerate(rows):
            for ci in range(cols):
                cell_parts = row_cells[ci] if ci < len(row_cells) else []
                cell = table.rows[ri].cells[ci]
                cell.text = ''
                write_parts(cell.paragraphs[0], cell_parts)

    def write_answer(row, metadata):
        text = (row['answer'] or '').strip()
        saved = metadata.get('answer_segments') or []
        import aianswers
        reviewed = aianswers.export_text(con, row['id'])
        if reviewed is not None:
            text, saved = reviewed, []
        # The current answer is authoritative; stale imported formatting must
        # not replace a later edit. Image-only answers also count as existing.
        saved_text = '\n'.join(item_plain(item) for item in saved).strip()
        if saved_text == text and any(item_nonempty(item) for item in saved):
            blocks = saved
        elif text:
            blocks = [[{'t': 'text', 's': line}] for line in text.splitlines()]
        else:
            return
        labelled = bool(re.match(r'^\s*(?:[【\[]?\s*(?:参考答案|答案|答[:：]))', text))
        first_answer = True
        for block in blocks:
            if isinstance(block, dict) and block.get('t') == 'table':
                if first_answer and not labelled:
                    label = doc.add_paragraph()
                    label.paragraph_format.keep_with_next = True
                    font_run(label.add_run('答案：'), bold=True)
                write_table(block.get('rows') or [])
            elif isinstance(block, list):
                paragraph = doc.add_paragraph()
                paragraph.paragraph_format.space_before = Pt(4 if first_answer else 0)
                paragraph.paragraph_format.space_after = Pt(2)
                if first_answer and not labelled:
                    font_run(paragraph.add_run('答案：'), bold=True)
                write_parts(paragraph, block)
            else:
                continue
            first_answer = False

    n = 0
    for qid in question_ids:
        row = con.execute("SELECT * FROM questions WHERE id=?", (int(qid),)).fetchone()
        if not row:
            con.close()
            raise RuntimeError("题目不存在，已停止导出：%s" % qid)
        n += 1
        segs = json.loads(row["segments"] or "[]")
        metadata = question_metadata(con, row)
        if any("图片关系缺失" in w or "未能读取的嵌入对象" in w for w in metadata["warnings"]):
            con.close()
            raise RuntimeError("题目存在未读取的图片或公式，请核对并修复后导出（题目 %s）" % qid)
        sources = source_labels(con, row["id"])
        first = True
        for para in segs:
            if isinstance(para, dict) and para.get("t") == "table":
                rows = para.get("rows") or []
                if not rows:
                    continue
                cols = max((len(r) for r in rows), default=0)
                if cols < 1:
                    continue
                if first and auto_number:
                    p = doc.add_paragraph()
                    r = p.add_run(f"{n}. ")
                    font_run(r)
                    first = False
                write_table(rows)
                continue
            if not isinstance(para, list):
                continue
            p = doc.add_paragraph()
            p.paragraph_format.space_after = Pt(2)
            p.paragraph_format.space_before = Pt(0)
            output_parts = []
            for part in para:
                if part.get("t") == "text" and first:
                    stext = part.get("s") or ""
                    if auto_number:
                        stext = re.sub(r"^\d{1,3}\s*[.．、:：]\s*", "", stext, count=1)
                        stext = f"{n}. " + stext
                    if auto_number and part.get("omml"):
                        output_parts.append({"t": "text", "s": f"{n}. "})
                        stext = part.get("s") or ""
                    part = dict(part, s=stext)
                    first = False
                    output_parts.append(part)
                elif part.get("t") == "img" and first:
                    if auto_number:
                        output_parts.append({"t": "text", "s": f"{n}. "})
                    first = False
                    output_parts.append(part)
                else:
                    output_parts.append(part)
            write_parts(p, output_parts)
        if first:
            p = doc.add_paragraph()
            body = row["body"] or ""
            if auto_number:
                body = f"{n}. " + re.sub(r"^\d{1,3}\s*[.．、:：]\s*", "", body, count=1)
            write_parts(p, [{"t": "text", "s": body}])
        if keep_answers:
            write_answer(row, metadata)
        if keep_source and sources:
            sp = doc.add_paragraph()
            sp.paragraph_format.space_before = Pt(2)
            r = sp.add_run("文件来源：" + "；".join(sources))
            font_run(r, size=9, color=(90, 90, 90))
        doc.add_paragraph()
    con.close()
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    doc.save(dest)
    return n


def ensure_paper_schema(con):
    """Papers the user composes from checked questions. Idempotent."""
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS papers (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS paper_items (
            paper_id INTEGER NOT NULL,
            question_id INTEGER NOT NULL,
            position INTEGER NOT NULL,
            UNIQUE(paper_id, question_id)
        );
        """
    )


def _paper_now():
    # Box clock is not Asia/Shanghai; the user reads times in that zone.
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")


def _clean_paper_name(name, fallback):
    if not isinstance(name, str):
        name = "" if name is None else str(name)
    name = name.strip()
    if not name:
        name = fallback
    if len(name) > 80:
        name = name[:80]
    return name


def _existing_question_ids(con, ids):
    """Dedupe, keep first-seen order, drop ids that are not in questions."""
    seen = set()
    out = []
    for raw in ids or []:
        try:
            qid = int(raw)
        except (TypeError, ValueError):
            continue
        if qid in seen:
            continue
        seen.add(qid)
        row = con.execute("SELECT 1 FROM questions WHERE id=?", (qid,)).fetchone()
        if row:
            out.append(qid)
    return out


def list_papers(con):
    rows = con.execute(
        """
        SELECT p.id, p.name, p.updated_at,
               (SELECT COUNT(*) FROM paper_items i
                JOIN questions q ON q.id = i.question_id
                WHERE i.paper_id = p.id) AS n
        FROM papers p
        ORDER BY p.updated_at DESC, p.id DESC
        """
    ).fetchall()
    return [
        {"id": r[0], "name": r[1], "updated_at": r[2], "count": r[3]}
        for r in rows
    ]


def get_paper(con, paper_id):
    try:
        paper_id = int(paper_id)
    except (TypeError, ValueError):
        return None
    row = con.execute(
        "SELECT id, name, updated_at FROM papers WHERE id=?", (paper_id,)
    ).fetchone()
    if not row:
        return None
    ids = [
        r[0]
        for r in con.execute(
            """
            SELECT i.question_id FROM paper_items i
            JOIN questions q ON q.id = i.question_id
            WHERE i.paper_id=?
            ORDER BY i.position, i.question_id
            """,
            (paper_id,),
        )
    ]
    return {"id": row[0], "name": row[1], "updated_at": row[2], "ids": ids}


def apply_paper(con, payload, paper_id=None):
    """Create or update a paper.

    Blank name is saved as 未命名试卷. ids are deduped and missing question
    ids are skipped. An empty id list does not create or wipe a paper.
    Omitting ids on an existing paper only renames it.
    """
    if not isinstance(payload, dict):
        payload = {}
    if paper_id in (None, ""):
        paper_id = payload.get("id")
    creating = paper_id in (None, "")
    if not creating:
        try:
            paper_id = int(paper_id)
        except (TypeError, ValueError):
            return None, "试卷不存在"
        row = con.execute("SELECT name FROM papers WHERE id=?", (paper_id,)).fetchone()
        if not row:
            return None, "试卷不存在"
        current_name = row[0]
    else:
        current_name = "未命名试卷"
    if "name" in payload:
        name = _clean_paper_name(payload.get("name"), "未命名试卷")
    else:
        name = _clean_paper_name(current_name, "未命名试卷")
    has_ids = "ids" in payload
    if creating and not has_ids:
        return None, "请至少选择一道题目"
    clean = None
    if creating or has_ids:
        clean = _existing_question_ids(con, payload.get("ids") or [])
        if not clean:
            return None, "请至少选择一道题目"
    now = _paper_now()
    if creating:
        cur = con.execute(
            "INSERT INTO papers (name, updated_at) VALUES (?,?)", (name, now)
        )
        paper_id = cur.lastrowid
    else:
        con.execute(
            "UPDATE papers SET name=?, updated_at=? WHERE id=?",
            (name, now, paper_id),
        )
    if clean is not None:
        current = [] if creating else _paper_item_ids(con, paper_id)
        keep = set(clean)
        kept = [qid for qid in current if qid in keep]
        kept_set = set(kept)
        fresh = [qid for qid in clean if qid not in kept_set]
        _write_paper_order(con, paper_id, insert_ids_by_type(con, kept, fresh))
    con.commit()
    return get_paper(con, paper_id), None


def delete_paper(con, paper_id):
    try:
        paper_id = int(paper_id)
    except (TypeError, ValueError):
        return False
    if not con.execute("SELECT 1 FROM papers WHERE id=?", (paper_id,)).fetchone():
        return False
    con.execute("DELETE FROM paper_items WHERE paper_id=?", (paper_id,))
    con.execute("DELETE FROM papers WHERE id=?", (paper_id,))
    con.commit()
    return True
