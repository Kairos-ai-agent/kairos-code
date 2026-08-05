"""Find stray top-level braces in Python files. A stray `}` at depth 0 is a
syntax error (we are NOT counting `}` inside docstrings / strings / comments).

This was the original cascade bug: orchestrator.py had a stray `}` at depth 0
that broke every test that imported it.
"""
import os, sys


def find_stray_braces(path, text):
    depth = 0
    i = 0
    in_str = None
    stray_close_at = None
    while i < len(text):
        c = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if in_str:
            if c == "\\":
                i += 2
                continue
            if in_str in ("\"\"\"", "'''"):
                if text[i : i + 3] == in_str:
                    in_str = None
                    i += 3
                    continue
            else:
                if c == in_str:
                    in_str = None
            i += 1
            continue
        if c == "#":
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        if c == '"' and nxt == '"' and text[i + 2 : i + 3] == '"':
            in_str = '"""'
            i += 3
            continue
        if c == "'" and nxt == "'" and text[i + 2 : i + 3] == "'":
            in_str = "'''"
            i += 3
            continue
        if c == '"' or c == "'":
            in_str = c
            i += 1
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth < 0 and stray_close_at is None:
                stray_close_at = i
        i += 1
    line = 0
    col = 0
    if stray_close_at is not None:
        line = text.count("\n", 0, stray_close_at) + 1
        col = stray_close_at - (text.rfind("\n", 0, stray_close_at) + 1) + 1
    return stray_close_at, line, col


roots = ["kairos", "api", "tests"]
issues = []
for root in roots:
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d != "__pycache__"]
        for f in fn:
            if not f.endswith(".py"):
                continue
            p = os.path.join(dp, f)
            try:
                with open(p, "r", encoding="utf-8") as fp:
                    text = fp.read()
            except (UnicodeDecodeError, OSError):
                continue
            off, line, col = find_stray_braces(p, text)
            if off is not None:
                issues.append((p, line, col))
if not issues:
    print("No stray top-level `}` found in any project file.")
else:
    for p, line, col in issues:
        print(f"{p}:{line}:{col} - stray closing brace at top level")