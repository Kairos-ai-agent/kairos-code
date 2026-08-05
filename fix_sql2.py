import re
with open(r'D:\software_bak\Kairos_code\kairos\core\persistence.py', encoding='utf-8') as f:
    content = f.read()
# Generic pattern: any line with "str",\n            , (params)
# Replace with: "str",\n            (params)
pattern = re.compile(r'\"([^\"]*)\",\n(\s+), \(', re.MULTILINE)
result = pattern.sub(lambda m: '"' + m.group(1) + '",\n' + m.group(2) + '(', content)
with open(r'D:\software_bak\Kairos_code\kairos\core\persistence.py', 'w', encoding='utf-8') as f:
    f.write(result)
print('Fixed.')
