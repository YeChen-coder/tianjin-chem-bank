"""Conservative selection for the user's bulk import of exam/exercise questions."""
from copy import deepcopy
from pathlib import Path
import re
from zipfile import ZipFile,is_zipfile
from lxml import etree
from .bank_metadata import question_text
from .storage import nodes

RESOURCE=re.compile(r'知识点|知识清单|知识梳理|知识汇总|知识手册|基础知识|必备知识|必背|背诵|默写|教研计划|教学计划|学习计划|教学内容|总结|答题纸|答题卡|答卷纸|易错字|易错点|细节易混|易错误区|答题技巧|题型分析|试卷分析|成绩分析|知识卡片|回归教材清单|教案|课件|通知|征集|试卷模板')
OTHER_SUBJECT=re.compile(r'诗词|道法|语文|英语|数学|尺规|二次函数|物理|历史|地理|生物|政治')
CHEM=re.compile(r'化学|氧气|二氧化碳|氢气|酸碱|溶液|氯化|硫酸|盐酸|原子|分子|元素|化合物|单质|质量守恒|金属|水垢|化学式|实验装置')
ASK=re.compile(r'下列|回答|计算|求出|填空|填写|填入|写出|选择|判断|说明|解释|分析并|完成|为多少|质量比为|质量分数为|[？?]|[_＿﹍]{2,}|(?:正确|错误|不合理).{0,8}(?:是|有)')
NUMBER=re.compile(r'^\s*(?:第\s*)?\d{1,3}\s*(?:题|[.．、:：)）])')


def filename_reason(path):
    name=Path(path).name
    if name.startswith('~$'):return 'Word 临时锁文件'
    if re.search(r'高中|高一|高二|高三|高考',name):return '高中资料，不属于当前九年级化学题库'
    if RESOURCE.search(name) or re.search(r'基础复习大全|复习提纲|速记',name):return '知识点/教学资料/答题纸等非试题文件'
    if OTHER_SUBJECT.search(name) and not CHEM.search(name):return '非化学学科文件'
    return None


def inspect_package(path):
    if not is_zipfile(path):return {'eligible':False,'reason':'需要先转换 DOC'}
    try:
        with ZipFile(path) as z:
            r=etree.fromstring(z.read('word/document.xml'),etree.XMLParser(resolve_entities=False,no_network=True))
        ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        paragraphs=[''.join(p.itertext()) for p in r.findall('.//w:body//w:p',ns)]
        text='\n'.join(paragraphs)
        chemistry=bool(CHEM.search(Path(path).stem)) or len(set(CHEM.findall(text)))>=2
        questions=sum(bool(NUMBER.match(p)) for p in paragraphs)
        asks=len(ASK.findall(text))
        options=len(re.findall(r'(?:^|\s)[A-DＡ-Ｄ][.．、:：]',text))
        eligible=chemistry and (questions>0 or options>=2 or asks>=2)
        return {'eligible':eligible,'reason':None if eligible else '正文未发现化学试题的可靠证据','numbered_paragraphs':questions,'asks':asks}
    except Exception as e:return {'eligible':False,'reason':'包读取失败：'+str(e),'error':True}


def select_questions(result):
    """Filter out numbered notes; preserve full question content and evidence."""
    selected=deepcopy(result);bundle=selected['bundle'];kept=[];removed=[]
    for q in bundle['questions']:
        text=question_text(q)
        genuine=len(q['options'])>=2 or bool(ASK.search(text))
        if genuine:kept.append(q)
        else:removed.append({'id':q['id'],'number':q['number'],'reason':'有编号但无选项、作答空或明确设问','excerpt':text[:160]})
    bundle['questions']=kept;bundle['paper']['question_ids']=[q['id'] for q in kept]
    ids={n['id'] for n in nodes(kept)};qids=set(bundle['paper']['question_ids'])
    bundle['issues']=[i for i in bundle['issues'] if i.get('question_id') in qids|{None} and (not i.get('node_id') or i['node_id'] in ids)]
    # Titles and teaching notes remain in the immutable source file only.
    # Relative atomic masses are conditions needed by actual questions.
    report=selected['report'];retained=report.get('retained_content_path')
    if retained:
        import json
        original=json.loads(Path(retained).read_text(encoding='utf-8'))
        allowed=[x for x in original if re.search(r'相对原子质量|相对分子质量',question_text({'body':[x['block']],'options':[],'subquestions':[]}))]
        target=Path(retained).with_name('bulk-retained-conditions.json')
        target.write_text(json.dumps(allowed,ensure_ascii=False),encoding='utf-8')
        report['retained_content_path']=str(target)
    report['question_count']=len(kept)
    return selected,removed
