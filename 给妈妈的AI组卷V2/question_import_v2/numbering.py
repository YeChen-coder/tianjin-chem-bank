import re
from xml.etree import ElementTree as ET

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
