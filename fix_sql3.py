import re
with open(r'D:\software_bak\Kairos_code\kairos\core\persistence.py', encoding='utf-8') as f:
    content = f.read()
pattern = re.compile(r'"([^"]*)",\n(\s+)"([^"]*)",\n(\s+)([^"\n])', re.MULTILINE)
def sub(m):
    indent = m.group(2)
    return '"' + m.group(1) + '" +\n' + indent + '"' + m.group(3) + '",\n' + m.group(4) + m.group(5)
result = pattern.sub(sub, content)
with open(r'D:\software_bak\Kairos_code\kairos\core\persistence.py', 'w', encoding='utf-8') as f:
    f.write(result)
print('Fixed 2-string.')
