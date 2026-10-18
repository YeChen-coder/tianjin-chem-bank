"""Parse typed chemical spans without discarding Word script information.

This is syntax recovery, not reaction balancing or OCR correction. Unknown
elements and ambiguous unstyled charges are rejected, not repaired by context.
"""
import re
from dataclasses import dataclass

ELEMENTS = set('H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og'.split())
SUPERSCRIPTS = dict(zip('⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻', '0123456789+-'))
SUBSCRIPTS = dict(zip('₀₁₂₃₄₅₆₇₈₉', '0123456789'))


@dataclass(frozen=True)
class Character:
    value: str
    style: str = 'baseline'


def characters(nodes):
    result = []
    for node in nodes:
        if node['kind'] == 'text':
            for char in node.get('text', ''):
                if char in SUPERSCRIPTS:
                    result.append(Character(SUPERSCRIPTS[char], 'superscript'))
                elif char in SUBSCRIPTS:
                    result.append(Character(SUBSCRIPTS[char], 'subscript'))
                else:
                    result.append(Character({'（':'(', '）':')'}.get(char,char), node.get('vert', 'baseline')))
        else:
            result.append(Character('\t' if node['kind'] == 'tab' else '\ufffc'))
    return result


class ChemicalSyntaxError(ValueError):
    pass


class Parser:
    def __init__(self, chars, start=0, placeholder_elements=()):
        self.chars, self.pos = chars, start
        self.placeholder_elements=set(placeholder_elements)

    def space(self):
        while self.pos < len(self.chars) and self.chars[self.pos].value in ' \t\u00a0':
            self.pos += 1

    def peek(self, style='baseline'):
        if self.pos < len(self.chars) and self.chars[self.pos].style == style:
            return self.chars[self.pos].value
        return ''

    def script(self, style):
        value = ''
        while self.pos < len(self.chars) and self.chars[self.pos].style == style:
            value += self.chars[self.pos].value
            self.pos += 1
        return value.replace('−', '-')

    def count(self):
        if self.peek('subscript'):
            value = self.script('subscript')
        else:
            value = ''
            while self.peek() and self.peek().isdigit() and self.peek().isascii():
                value += self.peek()
                self.pos += 1
        if value and (not value.isascii() or not value.isdigit() or int(value) == 0):
            raise ChemicalSyntaxError('Invalid chemical index')
        return '_{' + value + '}' if value else ''

    def coefficient(self):
        value = ''
        while self.peek() and self.peek().isascii() and self.peek().isdigit():
            value += self.peek()
            self.pos += 1
        if value and self.peek() == '.':
            value += '.'
            self.pos += 1
            digits = ''
            while self.peek() and self.peek().isascii() and self.peek().isdigit():
                digits += self.peek()
                self.pos += 1
            if not digits:
                raise ChemicalSyntaxError('Invalid coefficient')
            value += digits
        if value and self.peek() == '/':
            self.pos += 1
            denominator = ''
            while self.peek() and self.peek().isascii() and self.peek().isdigit():
                denominator += self.peek()
                self.pos += 1
            if not denominator or int(denominator) == 0:
                raise ChemicalSyntaxError('Invalid fractional coefficient')
            value = r'\frac{' + value + '}{' + denominator + '}'
        return value

    def phase(self):
        remaining = ''.join(c.value for c in self.chars[self.pos:self.pos+4] if c.style == 'baseline')
        match = re.match(r'\((aq|s|l|g)\)', remaining)
        return match[0] if match else ''

    def sequence(self, closing='', depth=0):
        if depth > 8:
            raise ChemicalSyntaxError('Chemical grouping is too deep')
        result, units = '', 0
        while self.pos < len(self.chars):
            char = self.peek()
            if units and self.phase():
                break
            if char and char in '([{（':
                end = {'(': ')', '[': ']', '{': '}', '（': '）'}[char]
                self.pos += 1
                content = self.sequence(end, depth + 1)
                if self.peek() != end:
                    raise ChemicalSyntaxError('Unclosed chemical group')
                self.pos += 1
                opening, ending = ('(', ')') if char == '（' else (char, end)
                if char == '{':
                    opening, ending = r'\{', r'\}'
                # The index belongs to the complete group, not just ')'.
                index=self.count()
                group=opening+content+ending
                result += ('{'+group+'}' if index else group) + index
                units += 1
            elif char and char.isascii() and char.isupper():
                symbol = char
                self.pos += 1
                if self.peek() and self.peek().isascii() and self.peek().islower():
                    symbol += self.peek()
                    self.pos += 1
                if symbol not in ELEMENTS and symbol not in self.placeholder_elements:
                    raise ChemicalSyntaxError('Unknown chemical element')
                result += r'\mathrm{' + symbol + '}' + self.count()
                units += 1
            else:
                break
        if not units:
            raise ChemicalSyntaxError('Empty chemical species')
        return result

    def species(self):
        self.space()
        coefficient = self.coefficient()
        self.space()
        if self.peek() == 'e':
            self.pos += 1
            body = r'\mathrm{e}'
            if not self.peek('superscript'):
                raise ChemicalSyntaxError('Electron requires explicit charge')
        else:
            body = self.sequence()
        while self.peek() and self.peek() in '·⋅':
            self.pos += 1
            part_coefficient = self.coefficient()
            body += r'\cdot ' + part_coefficient + self.sequence()
        if self.peek('superscript'):
            charge = self.script('superscript')
            if not re.fullmatch(r'(?:\d*[+-]|[+-]\d*)', charge):
                raise ChemicalSyntaxError('Unsupported superscript on chemical species')
            # A polyatomic charge belongs to the whole species, not its last atom.
            body = '{' + body + '}^{' + charge + '}'
        phase = self.phase()
        if phase:
            body += r'\mathrm{' + phase + '}'
            self.pos += len(phase)
        if self.peek() and self.peek() in '↑↓':
            body += r'\uparrow ' if self.peek() == '↑' else r'\downarrow '
            self.pos += 1
        return coefficient + body

    def expression(self):
        result = self.species()
        while True:
            end = self.pos
            self.space()
            if self.peek() != '+':
                self.pos = end
                break
            self.pos += 1
            result += '+' + self.species()
        if self.pos < len(self.chars):
            char = self.chars[self.pos]
            if char.style != 'baseline' or (char.value.isascii() and (char.value.isalnum() or char.value in '^+-)]}')):
                raise ChemicalSyntaxError('Ambiguous or incomplete formula boundary')
        return result, self.pos


def parse_prefix(nodes):
    chars = characters(nodes)
    parser = Parser(chars)
    try:
        latex, end = parser.expression()
        return {'start': 0, 'end': end, 'latex': latex}
    except ChemicalSyntaxError:
        return None


def parse_suffix(nodes):
    chars = characters(nodes)
    end = len(chars)
    while end and chars[end-1].value in ' \t\u00a0':
        end -= 1
    for start in range(end):
        char = chars[start]
        if char.style != 'baseline' or not (char.value.isascii() and (char.value.isupper() or char.value.isdigit()) or char.value in '([{（e'):
            continue
        if start and (chars[start-1].style != 'baseline' or chars[start-1].value.isascii() and (chars[start-1].value.isalnum() or chars[start-1].value in '^+-)]}')):
            continue
        parser = Parser(chars[:end], start)
        try:
            latex, stop = parser.expression()
            if stop == end:
                return {'start': start, 'end': len(chars), 'latex': latex}
        except ChemicalSyntaxError:
            pass
    return None
