"""Explainable suggestions, not official exam weights or an AI grading verdict."""
import json
import os
import re

ROOT = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(ROOT, "curriculum.json"), encoding="utf-8") as stream:
    CURRICULUM = json.load(stream)

TYPE_NAMES = {
    "单项选择": "单选题", "单项选择题": "单选题", "单选题": "单选题",
    "多项选择": "多选题", "多项选择题": "多选题", "多选题": "多选题",
    "不定项选择题": "多选题", "选择题": "单选题",
    "填空题": "填空题", "简答题": "简答题", "简答": "简答题",
    "实验题": "实验题", "实验与探究": "实验题", "实验探究题": "实验题",
    "计算题": "计算题", "计算": "计算题",
}


def section_type(text):
    text = re.sub(r"\s+", "", text or "")
    for name in sorted(TYPE_NAMES, key=len, reverse=True):
        if re.match(r"^(?:[一二三四五六七八九十]+[、.．:：])?" + name + r"(?:[（(:：]|$)", text):
            if TYPE_NAMES[name] == "单选题" and re.search(r"一或两|一个或两个|1个或2个|一至两|一到两|全部选对|选对但", text):
                return "多选题"
            return TYPE_NAMES[name]
    return None


def qtype_info(text, section=None):
    text = (text or "").translate(str.maketrans("ＡＢＣＤ", "ABCD"))
    warnings = []
    sub = set(re.findall(r"[（(]\s*([1-9]\d?)\s*[）)](?!\s*[/／])", text))
    # Require option boundaries and their order; apparatus A、B、C is not a block.
    opts = re.findall(r"(?:^|[\s\n])([A-D])\s*[.．:：、](?!\s*[A-D][、，,])", text)
    table_opts = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.split("|")]
        if len(cells) > 1:
            table_opts.extend(c for c in cells if re.fullmatch(r"[A-D]", c))
    ordered = "".join(opts)
    table_ordered = "".join(table_opts)
    has_options = "ABCD" in ordered or "ABCD" in table_ordered
    first_opt = re.search(r"(?:^|\s)A\s*[.．:：、]", text)
    before_opts = text[:first_opt.start()] if first_opt else text
    blank = bool(re.search(r"[_＿﹍▁]{2,}", text))
    composite = len(sub) >= 2 and bool(re.search(r"[_＿﹍▁]{2,}", before_opts))
    if section in {"填空题", "简答题", "实验题", "计算题"}:
        return {"qtype": section, "confidence": "high", "reason": "来自原卷题型标题", "warnings": warnings}
    if composite:
        typ = "实验题" if re.search(r"实验|探究|装置", text) else "填空题"
        reason, confidence = "主干含多个小问及作答空，局部选项不代表整道题", "medium"
    elif has_options or section in {"单选题", "多选题"}:
        multi = bool(re.search(r"多选|不定项|多项选择|正确的有|不正确的有|一个或两个|一至两", text))
        typ = "多选题" if multi or section == "多选题" else "单选题"
        reason, confidence = ("来自原卷题型标题", "high") if section else ("连续的 A 至 D 选项", "medium")
        if not has_options:
            warnings.append("选择题选项可能在图片中或提取不全，请核对")
    elif re.search(r"计算|列式|求(?:出)?[^。\n]{0,12}(?:质量|质量分数|体积)", text):
        typ, reason, confidence = "计算题", "定量计算设问", "medium"
    elif re.search(r"实验|探究|装置", text):
        typ, reason, confidence = "实验题", "实验或探究设问", "medium"
    elif blank:
        typ, reason, confidence = "填空题", "含作答空", "medium"
    else:
        typ, reason, confidence = "简答题", "未找到充分的题型证据", "low"
        warnings.append("题型证据不足，建议确认")
    return {"qtype": typ, "confidence": confidence, "reason": reason, "warnings": warnings}


def category_suggestions(body, answer, categories, threshold=3):
    ranked = []
    for cat in categories:
        # A one-character keyword such as “设” supplies no useful evidence.
        hits = [(kw, int(weight)) for kw, weight in cat["keywords"] if len(kw) >= 2 and kw in (body or "")]
        score = sum(weight for _, weight in hits)
        if score >= threshold:
            ranked.append({"major": cat["major"], "minor": cat["minor"], "score": score,
                           "evidence": [kw for kw, _ in hits]})
    ranked.sort(key=lambda cat: (-cat["score"], -max(map(len, cat["evidence"]))))
    if not ranked:
        return []
    # Keep more than one substantive topic, but don't label every mentioned reagent.
    floor = max(threshold, ranked[0]["score"] * 0.55)
    return [cat for cat in ranked if cat["score"] >= floor][:6]


def knowledge_info(body):
    topics = []
    for point in CURRICULUM["points"]:
        hits = [word for word in point["keywords"] if word.casefold() in (body or "").casefold()]
        if hits:
            topics.append({"id": point["id"], "name": point["name"], "theme": point["theme"], "evidence": hits})
    return {"version": CURRICULUM["version"], "basis": CURRICULUM["basis"],
            "themes": list(dict.fromkeys(p["theme"] for p in topics)), "points": topics,
            "status": "suggested" if topics else "unclassified"}


def curriculum_prompt():
    return ("课程约束：参考义务教育化学课程标准及其日常修订版，面向天津九年级；"
            "知识、能力与真实情境共同考查，不使用高中离子反应机理、化学平衡、电化学计算等内容。"
            "不得把本项目知识点标签或关键词分数当成天津官方考试权重。"
            "原图中的装置、曲线、标号、物质及数值均锁定，不能改题导致与图冲突。"
            "实验题须核对变量控制、证据与结论、安全和操作可行性；计算须重新校验质量守恒、单位和数据。"
            "多选题可以有多个正确选项，应明确完整答案；保持原题的作答形式和小问结构。")
