import os, ast
root = 'kairos'
issues = []
for dp, dn, fn in os.walk(root):
    dn[:] = [d for d in dn if d != '__pycache__']
    for f in fn:
        if not f.endswith('.py'):
            continue
        p = os.path.join(dp, f)
        try:
            with open(p, 'r', encoding='utf-8') as fp:
                src = fp.read()
            ast.parse(src, filename=p)
        except SyntaxError as e:
            issues.append((p, e.lineno, e.msg, (e.text or '').rstrip()))
if not issues:
    print('ALL FILES OK')
else:
    for p, ln, m, t in issues:
        print(f'{p}:{ln} - {m}')
        print(f'  >>> {t}')