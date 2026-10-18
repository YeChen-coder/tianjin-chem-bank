"""Production formula path: source structures/text → LaTeX → native Word.

Stored question text, IDs, labels and image bytes remain authoritative. Display
normalization is cached separately; a teacher edit is an explicit write.
"""
import copy,hashlib,json,os,re,threading,uuid
from pathlib import Path
from lxml import etree as E
from .latex_adapter import convert_latex
from .math_codec import normalize_mathml,mathml_to_latex
from .omml_reader import omml_to_mathml
from .native_repairs import repair_accent_bars,repair_group_braces,repair_empty_script_bases
from .word_mathml import prepare_word_mathml
from .formula_integrity import validate_word_conversion
from .formula_spans import suggest_condition_merge
from .review_plan import text_candidates
from .inline_model import txt,slice_nodes
M='http://schemas.openxmlformats.org/officeDocument/2006/math'
PARSER=E.XMLParser(resolve_entities=False,no_network=True)
VERSION='latex-formulas-s4-v2';LOCK=threading.RLock()

def digest(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def atomic_json(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:temporary.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8');temporary.replace(path)
    finally:temporary.unlink(missing_ok=True)

def native_formula(omml):
    mathml=omml_to_mathml(omml)
    return {'omml':omml,'mathml':mathml,'latex':mathml_to_latex(mathml)}

def _basic_omml(mathml):
    """Portable chemical/basic-math subset; unsupported layouts fail intact."""
    root=E.fromstring(mathml.encode(),PARSER)
    def elem(name,children=()):
        value=E.Element('{'+M+'}'+name);value.extend(children);return value
    def convert(node,style=None):
        name=E.QName(node).localname;style=node.get('mathvariant',style)
        if name in {'math','mrow','mstyle'}:return [part for child in node for part in convert(child,style)]
        if name in {'mi','mn','mo','mtext'}:
            run=elem('r');props=elem('rPr');kind={'normal':'p','bold':'b','bold-italic':'bi','italic':'i'}.get(style)
            if style and not kind:raise ValueError('这种数学字体需要 Office 公式转换支持')
            if kind:
                val=elem('sty');val.set('{'+M+'}val',kind);props.append(val);run.append(props)
            value=elem('t');value.text=node.text or '';run.append(value);return [run]
        structures={'msub':('sSub',['e','sub']),'msup':('sSup',['e','sup']),'msubsup':('sSubSup',['e','sub','sup']),'mfrac':('f',['num','den']),'mroot':('rad',['e','deg']),'mover':('limUpp',['e','lim']),'munder':('limLow',['e','lim'])}
        if name=='msqrt':return [elem('rad',[elem('deg'),elem('e',[p for c in node for p in convert(c,style)])])]
        if name in structures:
            kind,slots=structures[name]
            if len(node)!=len(slots):raise ValueError('公式结构不完整')
            values=list(node)
            if name=='mroot':values=values[::-1]
            return [elem(kind,[elem(slot,convert(child,style)) for slot,child in zip(slots,values)])]
        if name=='mspace':return []
        raise ValueError('这种公式结构需要 Office 公式转换支持：'+name)
    native=elem('oMath',convert(root));return E.tostring(native,encoding='unicode')

def office_xsl():
    candidates=[os.environ.get('CHEM_MML2OMML_XSL',''),r'C:\Program Files\Microsoft Office\root\Office16\MML2OMML.XSL',r'C:\Program Files (x86)\Microsoft Office\root\Office16\MML2OMML.XSL']
    return next((Path(p) for p in candidates if p and Path(p).is_file()),None)

def compile_formula(latex):
    if not isinstance(latex,str) or not latex.strip() or len(latex)>100000:raise ValueError('请输入完整公式')
    with LOCK:
        source=normalize_mathml(convert_latex(latex),latex)
        mathml=E.tostring(source,encoding='unicode')
        xsl=office_xsl()
        if xsl:
            target=E.XSLT(E.parse(str(xsl),PARSER))(prepare_word_mathml(source)).getroot()
            if E.QName(target).localname=='oMathPara':target=target.find('{'+M+'}oMath')
            omml=E.tostring(target,encoding='unicode')
        else:omml=_basic_omml(mathml)
        omml,_=repair_accent_bars(mathml,omml);omml,_=repair_group_braces(mathml,omml);omml=repair_empty_script_bases(omml)
        validate_word_conversion(mathml,omml)
        return {'latex':latex,'mathml':mathml,'omml':omml}

def _nodes(parts,cache_dir):
    nodes=[]
    for position,part in enumerate(parts):
        if part.get('t')=='text':
            formula=None
            if part.get('omml'):
                try:formula=native_formula(part['omml'])
                except (ValueError,E.XMLSyntaxError,TypeError) as error:
                    retained=copy.deepcopy(part)
                    retained['formula_issue']={'code':'UNSUPPORTED_SOURCE','message':str(error)}
                    nodes.append({'kind':'opaque','id':digest([position,part])[:24],'bank_part':retained})
                    continue
            if formula:
                nodes.append({'kind':'math','id':digest([position,part])[:24],**formula,'bank_part':part});continue
            text=part.get('s','');position=0
            for match in re.finditer(r'\^\{([^{}]*)\}',text):
                if match.start()>position:nodes.append({'kind':'text','text':text[position:match.start()],'vert':part.get('vert'),'bank_part':part})
                nodes.append({'kind':'text','text':match[1],'vert':'superscript','bank_part':part});position=match.end()
            if position<len(text):nodes.append({'kind':'text','text':text[position:],'vert':part.get('vert'),'bank_part':part})
        elif part.get('t')=='img':
            reference=Path(cache_dir)/'references'/((part.get('sha') or '')+'.json')
            formula=None
            if reference.is_file():
                try:
                    record=json.loads(reference.read_text(encoding='utf-8'))
                    if len(record.get('formulas',[]))==1:formula=record['formulas'][0]
                except (ValueError,OSError):pass
            if formula:nodes.append({'kind':'math','id':digest([position,part])[:24],**formula,'bank_part':part,'source_image':part})
            else:nodes.append({'kind':'image','bank_part':part})
        else:nodes.append({'kind':'opaque','bank_part':part})
    return nodes

def _parts(nodes):
    result=[]
    for node in nodes:
        part=copy.deepcopy(node.get('bank_part',{}))
        if node['kind']=='math':
            if not node.get('omml'):
                try:node.update(compile_formula(node['latex']))
                except (ValueError,E.XMLSyntaxError) as error:
                    original=_parts(node.get('source_parts',[]))
                    if not original and part:original=[part]
                    if not original:original=[{'t':'text','s':node.get('latex','')}]
                    for retained in original:retained['formula_issue']={'code':'FORMULA_COMPILE_FAILED','message':str(error)}
                    result.extend(original);continue
            if part.get('t')!='text':part={'t':'text','s':txt(node.get('source_parts',[])).replace('\ufffc','') or node.get('latex','')}
            part.update({k:node[k] for k in ('latex','mathml','omml')});part['formula_version']=VERSION
            if node.get('source_image'):part['source_image']=copy.deepcopy(node['source_image'])
            if node.get('source_parts'):part['source_parts']=_parts(node['source_parts'])
            result.append(part)
        elif node['kind']=='text':
            part.update(t='text',s=node.get('text',''))
            if node.get('vert')=='superscript':part['s']='^{'+part['s']+'}';part.pop('vert',None)
            elif node.get('vert')=='subscript':part['vert']='subscript'
            else:part.pop('vert',None)
            result.append(part)
        else:result.append(part)
    return result

def normalize_parts(parts,cache_dir):
    if not isinstance(parts,list):return parts
    parts=copy.deepcopy(parts);key=digest([VERSION,parts]);path=Path(cache_dir)/'parts'/(key+'.json')
    # Reference revision is included so recovered OLE previews invalidate old
    # displays without rewriting the question or its deduplication identity.
    refs=Path(cache_dir)/'references';revision=refs.stat().st_mtime_ns if refs.is_dir() else 0
    if path.is_file():
        try:
            saved=json.loads(path.read_text(encoding='utf-8'))
            if saved.get('revision')==revision:return saved['parts']
        except (ValueError,OSError):pass
    nodes=_nodes(parts,cache_dir);block={'kind':'paragraph','id':'formula','inlines':nodes}
    for node in list(nodes):
        if node['kind']!='math' or not node.get('mathml'):continue
        merge=suggest_condition_merge(block,node['id'],node['latex'],node['mathml'])
        if merge:block['inlines']=[*merge['before'],{'kind':'math','latex':merge['latex'],'source_parts':merge['source_parts']},*merge['after']]
    for candidate in reversed(list(text_candidates(block))):
        if candidate['mode']!='chemistry':continue
        block['inlines']=[*slice_nodes(block['inlines'],0,candidate['start']),{'kind':'math','latex':candidate['latex'],'source_parts':candidate['source_parts']},*slice_nodes(block['inlines'],candidate['end'],len(txt(block['inlines'])))]
    result=_parts(block['inlines'])
    for i,part in enumerate(result):
        if part.get('latex'):part['formula_id']=digest([key,i])[:32]
    atomic_json(path,{'revision':revision,'parts':result});return result

def normalize_segments(segments,cache_dir):
    result=[]
    for block in segments:
        if isinstance(block,list):result.append(normalize_parts(block,cache_dir))
        elif isinstance(block,dict) and block.get('t')=='table':result.append({**block,'rows':[[normalize_parts(cell,cache_dir) for cell in row] for row in block.get('rows',[])]})
        else:result.append(copy.deepcopy(block))
    return result
