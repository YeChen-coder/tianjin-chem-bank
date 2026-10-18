"""Deterministic legacy Symbol-font decoding, separate from chemistry semantics.

Character identities checked against:
https://www.unicode.org/Public/MAPPINGS/VENDORS/ADOBE/symbol.txt
Unknown fonts/codes are not inferred from question meaning.
"""
from pathlib import Path

# Keep the upstream notice in the bundled table. Private glyph pieces have no
# portable identity in this table and remain unresolved instead of becoming
# an invented complete bracket, radical or arrow.
SYMBOL_CODES = {}
for line in (Path(__file__).parent / 'vendor' / 'adobe-symbol.txt').read_text(encoding='utf-8').splitlines():
    if not line or line.startswith('#'):continue
    value, code, *_ = line.split()
    value, code = int(value,16), int(code,16)
    if not 0xE000 <= value <= 0xF8FF:SYMBOL_CODES.setdefault(code,chr(value))
# Prefer the Greek letter identity consistently with Delta and Omega. The
# table lists both the micro sign and Greek mu for the same font glyph.
SYMBOL_CODES[0x6D]='μ'
SYMBOL_DECODE_VERSION=2


def decode_symbol(code, font):
    """Return standard Unicode, or None if a glyph requires further examination."""
    if (font or "").strip().casefold() == "symbol":
        code = code - 0xF000 if 0xF000 <= code <= 0xF0FF else code
        return SYMBOL_CODES.get(code)
    return chr(code) if code < 0xE000 else None


def decode_symbol_text(text, fonts):
    """Decode private Symbol codes in w:t as well as font-coded ASCII operators."""
    symbol_fonts = [name for name in fonts.values() if name.strip().casefold() == "symbol"]
    if not symbol_fonts:
        return text, []
    result, unknown = [], []
    for char in text:
        code = ord(char)
        font=fonts.get('ascii',fonts.get('hAnsi','')) if code<0x80 else fonts.get('hAnsi',fonts.get('ascii',''))
        latin_symbol=font.strip().casefold()=='symbol'
        if 0xF000 <= code <= 0xF0FF or (latin_symbol and 0x20 <= code <= 0xFF):
            normalized = decode_symbol(code, "Symbol")
            if normalized is None:
                unknown.append(code)
                result.append("□")
            else:
                result.append(normalized)
        else:
            result.append(char)
    return "".join(result), unknown


def symbol_text_segments(text,fonts):
    """Split unknown glyphs so their exact font code survives a Word export."""
    pending=[]
    for char in text:
        normalized,unknown=decode_symbol_text(char,fonts)
        if unknown:
            if pending:
                yield ''.join(pending),None
                pending=[]
            yield normalized,unknown[0]
        else:pending.append(normalized)
    if pending:yield ''.join(pending),None
