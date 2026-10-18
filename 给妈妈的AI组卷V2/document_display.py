"""Presentation of answer blanks and legacy source names without changing stored data."""
from functools import lru_cache
from pathlib import Path
import re
import zipfile
from xml.etree import ElementTree as ET

HORIZONTAL_SPACE = " \t\u00a0\u2000-\u200a\u202f\u3000"
BLANK = re.compile(r"_(?:[" + HORIZONTAL_SPACE + r"]*_)*")
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def blank_length(text):
    return sum(2 if c == "\u3000" else 1 for c in text)


def presentation_parts(parts):
    """Join equivalent text runs so a blank split by Word run boundaries stays whole."""
    result = []
    for part in parts:
        part = dict(part)
        if (result and part.get("t") == "text" and not part.get("omml")
                and {k: v for k, v in part.items() if k != "s"}
                == {k: v for k, v in result[-1].items() if k != "s"}):
            result[-1]["s"] = (result[-1].get("s") or "") + (part.get("s") or "")
        else:
            result.append(part)
    return result


def abnormal_filename(name):
    # Restrict to characteristic legacy mojibake; ordinary Chinese/Latin names stay intact.
    return bool(re.search(r"[\ufffd\uffe1\u2500-\u259f]", name)
                or ("_" in name and len(re.findall(r"[\u00c0-\u024f\u0370-\u03ff]", name)) >= 2))


@lru_cache(maxsize=256)
def _document_title(path, size, mtime_ns):
    try:
        with zipfile.ZipFile(path) as archive:
            candidates = []
            if "docProps/core.xml" in archive.namelist():
                props = ET.fromstring(archive.read("docProps/core.xml"))
                node = props.find("{http://purl.org/dc/elements/1.1/}title")
                if node is not None:
                    candidates.append(node.text or "")
            body = ET.fromstring(archive.read("word/document.xml")).find(W + "body")
            if body is not None:
                for paragraph in body.findall(W + "p")[:12]:
                    text = "".join(n.text or "" for n in paragraph.iter(W + "t")).strip()
                    if re.match(r"^(?:第\s*)?\d{1,3}\s*[.．、:：)）题]", text):
                        break
                    candidates.append(text)
            for text in candidates:
                text = re.sub(r"\s+", " ", text).strip()
                if (4 <= len(text) <= 100 and re.search(r"[\u4e00-\u9fff]", text)
                        and re.search(r"试题|试卷|考试|测试|练习|模拟题|习题", text)
                        and not re.search(r"本大题|选项|相对原子质量|注意事项", text)
                        and not abnormal_filename(text)):
                    return text
    except (OSError, KeyError, ValueError, zipfile.BadZipFile, ET.ParseError):
        pass
    return ""


def source_info(rel_path, qnum, roots):
    """Read a title from an available local DOCX; never guess or rename the source file."""
    rel_path = str(rel_path or "")
    name = rel_path.replace("\\", "/").rsplit("/", 1)[-1]
    abnormal = abnormal_filename(name)
    title = ""
    if abnormal and name.lower().endswith(".docx"):
        for root in roots:
            root = Path(root).resolve()
            path = (root / rel_path.replace("\\", "/")).resolve()
            if not path.is_relative_to(root):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            title = _document_title(str(path), stat.st_size, stat.st_mtime_ns)
            if title:
                break
    label = ("文档标题：" + title if title else "未命名来源") if abnormal else rel_path
    suffix = []
    if abnormal:
        suffix.append("原文件名编码异常")
    if str(qnum or "").strip():
        suffix.append("原题号 " + str(qnum).strip())
    if suffix:
        label += "（" + "；".join(suffix) + "）"
    return {"label": label, "rel_path": rel_path, "document_title": title,
            "name_status": "abnormal" if abnormal else "original"}
