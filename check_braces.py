import os, re, sys

def check_brace_balance(path, text):
    """Check that the file's brace balance ends at 0. We do a lightweight
    pass that ignores braces inside string literals (single/double/triple
    quoted) and comments. This catches the kind of stray `}` that caused
    the `orchestrator.py:689` cascade earlier."""
    depth = 0
    i = 0
    in_str = None  # None or '\'' or '"' or '"""' or "'''"
    while i < len(text):
        c = text[i]
        nxt = text[i+1] if i + 1 < len(text) else ''
        prev = text[i-1] if i > 0 else ''
        if in_str:
            if c == '\\':
                i += 2
                continue
            if in_str in ('"""', "'''"):
                if text[i:i+3] == in_str:
                    in_str = None
                    i += 3
                    continue
            else:
                if c == in_str:
                    in_str = None
            i += 1
            continue
        if c == '#':
            # comment until newline
            while i < len(text) and text[i] != '\n':
                i += 1
            continue
        if c == '"' and nxt == '"' and text[i+2:i+3] == '"':
            in_str = '"""'
            i += 3
            continue
        if c == "'" and nxt == "'" and text[i+2:i+3] == "'":
            in_str = "'''"
            i += 3
            continue
        if c == '"' or c == "'":
            in_str = c
            i += 1
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
        i += 1
    return depth

roots = ['kairos', 'api', 'tests']
issues = []
for root in roots:
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d != '__pycache__']
        for f in fn:
            if not f.endswith('.py'):
                continue
            p = os.path.join(dp, f)
            try:
                with open(p, 'r', encoding='utf-8') as fp:
                    text = fp.read()
            except (UnicodeDecodeError, OSError):
                continue
            d = check_brace_balance(p, text)
            if d != 0:
                issues.append((p, d))
if not issues:
    print('ALL FILES HAVE BALANCED BRACES (or none)')
else:
    for p, d in issues:
        print(f'{p}: imbalance depth={d}')