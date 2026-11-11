#!/usr/bin/env python3
"""Narrow sv2v compatibility pass; not a general Verilog constant evaluator.

Only sized b/o/d/h literals with known bits and unsigned decimal indices are
accepted. Selects are unsigned, including selects of signed literals. Unknown,
variable, negative, out-of-range and chained selects are left for the frontend.
Comments, strings, escaped identifiers and preprocessor lines are opaque.
"""
import bisect
import re

MAX_BITS = 1_000_000
LEX = re.compile(
    r'(?P<opaque>//[^\n]*|/\*[\s\S]*?\*/|"(?:\\[\s\S]|[^"\\])*"|'
    r'\\[^\s]+|`(?:\\\r?\n|[^\n])*)'
    r'|(?P<sized>[0-9][0-9_]*\s*\'\s*[sS]?[bBoOdDhH]\s*[0-9a-fA-F_xXzZ?]+)'
    r'|(?P<othernum>\'\s*[sS]?[bBoOdDhH]\s*[0-9a-fA-F_xXzZ?]+|\'[01xXzZ])'
    r'|(?P<ident>[a-zA-Z_$][a-zA-Z0-9_$]*)'
    r'|(?P<decimal>[0-9][0-9_]*)'
    r'|(?P<space>\s+)|(?P<symbol>\+:|-:|.)'
)
LITERAL = re.compile(r"([0-9][0-9_]*)\s*'\s*([sS]?)([bBoOdDhH])\s*([0-9a-fA-F_xXzZ?]+)")


def fold_literal_slices(source):
    tokens = [m for m in LEX.finditer(source) if m.lastgroup != 'space']
    newlines = [i for i, c in enumerate(source) if c == '\n']
    edits = []
    for i, literal in enumerate(tokens):
        if literal.lastgroup != 'sized' or i + 3 >= len(tokens):
            continue
        if tokens[i + 1].group() != '[' or tokens[i + 2].lastgroup != 'decimal':
            continue
        end = i + 3
        op = tokens[end].group()
        if op != ']':
            if op not in (':', '+:', '-:') or i + 5 >= len(tokens):
                continue
            if tokens[i + 4].lastgroup != 'decimal' or tokens[i + 5].group() != ']':
                continue
            end = i + 5
        # No lexical adjacency ambiguity, embedded comments, or chained select.
        # +: and -: are single lexer tokens. Their width operand can itself
        # be a literal slice, with no whitespace after the operator.
        if i and tokens[i - 1].end() == literal.start() and tokens[i - 1].group() not in ('(', '=', ',', ':', '[', '+', '-', '*', '/', '&', '|', '^', '~', '!','?', '{', '+:', '-:'):
            continue
        if end + 1 < len(tokens) and tokens[end + 1].group() == '[':
            continue
        if any(source[tokens[j].end():tokens[j+1].start()].strip() for j in range(i, end)):
            continue
        m = LITERAL.fullmatch(literal.group())
        width_s, signed, base_s, digits = m.groups()
        try:
            width = int(width_s.replace('_', ''))
        except ValueError:
            continue
        if not 0 < width <= MAX_BITS or any(c in digits.lower() for c in 'xz?'):
            continue
        radix = {'b': 2, 'o': 8, 'd': 10, 'h': 16}[base_s.lower()]
        try:
            value = int(digits.replace('_', ''), radix) & ((1 << width) - 1)
            first = int(tokens[i+2].group().replace('_', ''))
            second = int(tokens[i+4].group().replace('_', '')) if op != ']' else 1
        except ValueError:
            continue
        if op == ':':
            low, count = second, first - second + 1
        elif op == '-:':
            low, count = first - second + 1, second
        elif op == '+:':
            low, count = first, second
        else:
            low, count = first, 1
        if count <= 0 or low < 0 or low + count > width:
            continue
        selected = (value >> low) & ((1 << count) - 1)
        start, stop = literal.start(), tokens[end].end()
        original = source[start:stop]
        replacement = f"{count}'h{selected:x}"
        edits.append({'start': start, 'end': stop,
                      'line': bisect.bisect_left(newlines, start) + 1,
                      'original': original, 'replacement': replacement,
                      'literal_width': width, 'lsb': low, 'select_width': count})
    result = source
    for edit in reversed(edits):
        # Keep line numbers stable for Yosys diagnostics.
        result = (result[:edit['start']] + edit['replacement'] +
                  '\n' * edit['original'].count('\n') + result[edit['end']:])
    return result, edits
