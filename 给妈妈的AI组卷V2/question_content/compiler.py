"""One production bridge; cache keyed by complete LaTeX and compiler identity."""
from copy import deepcopy
from functools import lru_cache
import hashlib
from pathlib import Path
from formula_engine import engine


def compiler_version():
    xsl = engine.office_xsl()
    fingerprint = hashlib.sha256(xsl.read_bytes()).hexdigest()[:16] if xsl else 'portable-subset'
    return 's4-latex2mathml-3.78.1-v3/'+fingerprint


@lru_cache(maxsize=512)
def _compile(latex, version):
    result = engine.compile_formula(latex)
    return {'ok':True, **result, 'compiler_version':version, 'diagnostics':[]}


def compile_formula(latex, source_ref=None):
    version = compiler_version()
    try:
        if not isinstance(latex,str) or not latex.strip() or len(latex)>16000:
            raise ValueError('请输入不超过 16000 字符的完整公式')
        depth=0;i=0
        while i<len(latex):
            if latex[i]=='\\' and i+1<len(latex) and latex[i+1] in '{}\\':i+=2;continue
            if latex[i]=='{':depth+=1
            elif latex[i]=='}':
                depth-=1
                if depth<0:raise ValueError('公式花括号不匹配')
            i+=1
        if depth:raise ValueError('公式花括号不匹配')
        return deepcopy(_compile(latex,version))
    except Exception as exc:
        return {'ok':False,'latex':latex,'mathml':None,'omml':None,'compiler_version':version,
                'diagnostics':[{'code':'FORMULA_COMPILE_FAILED','message':str(exc),'source_ref':source_ref}]}
