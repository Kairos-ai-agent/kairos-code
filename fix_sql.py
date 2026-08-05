import re
with open(r'D:\software_bak\Kairos_code\kairos\core\persistence.py', encoding='utf-8') as f:
    content = f.read()
# Find patterns like:
#   "str1",
#       "str2",
#           , (params)
# and convert to:
#   "str1" +
#       "str2",
#           (params)
pattern = re.compile(r'\"([^\"]*)\",\n(\s+)\"([^\"]*)\",\n(\s+), \(', re.MULTILINE)
result = pattern.sub(lambda m: '"' + m.group(1) + '" +\n' + m.group(2) + '"' + m.group(3) + '",\n' + m.group(4) + '(', content)
# Also handle the 3-string case
pattern2 = re.compile(r'\"([^\"]*)\",\n(\s+)\"([^\"]*)\",\n(\s+)\"([^\"]*)\",\n(\s+), \(', re.MULTILINE)
def sub2(m):
    return '"' + m.group(1) + '" +\n' + m.group(2) + '"' + m.group(3) + '" +\n' + m.group(4) + '"' + m.group(5) + '",\n' + m.group(6) + '('
result = pattern2.sub(sub2, result)
with open(r'D:\software_bak\Kairos_code\kairos\core\persistence.py', 'w', encoding='utf-8') as f:
    f.write(result)
print('Fixed.')
