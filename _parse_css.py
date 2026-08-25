"""Extract design tokens and layout selectors from the DSH CSS."""
import re
css_path = r"D:\software_bak\Kairos_code\_dsh_extract\node_modules\@deepseek-ai\dsh-web-frontend\dist\assets\index-C6eRlFa6.css"
css = open(css_path, encoding="utf-8").read()

# Extract --dsh-* CSS custom properties
tokens = re.findall(r'(--dsh-[\w-]+\s*:\s*[^;]+;)', css)
print(f"Found {len(tokens)} design tokens (--dsh-*)")
for t in tokens[:80]:
    s = t.strip()
    if len(s) > 140:
        s = s[:140] + "..."
    print(" ", s)

# Also look for layout-related selectors
print()
print("=== Layout-related selectors ===")
for s in re.findall(r'(\._?(?:sidebar|main|chat|nav|app|root|topbar|header|footer|content|panel|list|message)[\w_-]*\s*\{[^}]+\})', css)[:20]:
    print(s[:250])
    print()

# And grid / flex declarations
print("=== Grid/flexbox usage ===")
for s in re.findall(r'(display\s*:\s*(?:grid|flex)[^;}]+[;}])', css)[:10]:
    print(" ", s[:200])

# Class names
print()
print("=== Class names that look like structural ===")
classes = sorted(set(re.findall(r'\._?([a-z][a-z0-9]+(?:[A-Z][a-z]+)?)_[\w]+', css)))
struct = [c for c in classes if any(k in c.lower() for k in
          ['sidebar', 'main', 'chat', 'nav', 'app', 'root', 'topbar',
           'header', 'footer', 'content', 'panel', 'list', 'message',
           'message', 'role', 'tool', 'thread', 'input', 'send'])]
print("Structural classes:", struct[:30])
