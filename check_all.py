import ast
import os
root = r'D:\software_bak\Kairos_code'
ok = True
for dirpath, dirnames, filenames in os.walk(root):
    if '__pycache__' in dirpath or '.venv' in dirpath or 'workspace' in dirpath:
        continue
    for f in filenames:
        if not f.endswith('.py'):
            continue
        path = os.path.join(dirpath, f)
        try:
            with open(path, encoding='utf-8') as fp:
                ast.parse(fp.read())
        except SyntaxError as e:
            print(f'SYNTAX ERROR in {path}: {e}')
            ok = False
print('ALL OK' if ok else 'SOME FAILED')
