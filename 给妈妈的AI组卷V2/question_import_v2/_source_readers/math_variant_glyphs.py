"""Unicode math alphabets for MathML Core's restricted mathvariant support."""
import unicodedata
PREFIXES=[('SANS-SERIF BOLD ITALIC','sans-serif-bold-italic'),('SANS-SERIF BOLD','bold-sans-serif'),('SANS-SERIF ITALIC','sans-serif-italic'),('BOLD ITALIC','bold-italic'),('BOLD SCRIPT','bold-script'),('BOLD FRAKTUR','bold-fraktur'),('DOUBLE-STRUCK','double-struck'),('SANS-SERIF','sans-serif'),('MONOSPACE','monospace'),('SCRIPT','script'),('FRAKTUR','fraktur'),('BOLD','bold'),('ITALIC','italic')]
def glyph_tables():
    result={variant:{} for _,variant in PREFIXES}
    for code in [*range(0x1D400,0x1D800),*range(0x2100,0x2150)]:
        char=chr(code);name=unicodedata.name(char,'');plain=unicodedata.normalize('NFKC',char)
        if len(plain)!=1 or plain==char:continue
        label=name.removeprefix('MATHEMATICAL ')
        if name=='PLANCK CONSTANT':label='ITALIC SMALL H'
        if name.startswith('BLACK-LETTER '):label='FRAKTUR '+name[len('BLACK-LETTER '):]
        for prefix,variant in PREFIXES:
            if label.startswith(prefix+' '):result[variant].setdefault(plain,char);break
    return result
