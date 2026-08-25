"""Minimal S-expression parser for KiCad file formats."""
from __future__ import annotations


def parse(text: str):
    tokens = _tokenize(text)
    pos = 0

    def read():
        nonlocal pos
        tok = tokens[pos]
        pos += 1
        if tok == "(":
            out = []
            while tokens[pos] != ")":
                out.append(read())
            pos += 1
            return out
        return tok

    expr = read()
    if pos != len(tokens):
        raise ValueError("trailing content after top-level s-expression")
    return expr


def _tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif c in "()":
            tokens.append(c)
            i += 1
        elif c == '"':
            j = i + 1
            buf = []
            while j < n and text[j] != '"':
                if text[j] == "\\" and j + 1 < n:
                    buf.append(text[j + 1])
                    j += 2
                else:
                    buf.append(text[j])
                    j += 1
            tokens.append("".join(buf))
            i = j + 1
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in '()"':
                j += 1
            tokens.append(text[i:j])
            i = j
    return tokens


def find(expr: list, key: str) -> list | None:
    """First child list whose head is `key`."""
    for item in expr:
        if isinstance(item, list) and item and item[0] == key:
            return item
    return None


def find_all(expr: list, key: str) -> list[list]:
    return [item for item in expr
            if isinstance(item, list) and item and item[0] == key]


def atom(expr: list, key: str, default: str = "") -> str:
    """Value of a (key value) child."""
    node = find(expr, key)
    if node and len(node) > 1 and isinstance(node[1], str):
        return node[1]
    return default
