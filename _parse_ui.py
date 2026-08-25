"""Look at dsh-client-ui-primitives for design system components."""
import os, re
base = r"D:\software_bak\Kairos_code\_dsh_extract\node_modules\@deepseek-ai\dsh-client-ui-primitives"

# List all CSS files (component styles)
print("=== dsh-client-ui-primitives components ===")
for f in sorted(os.listdir(os.path.join(base, "lib"))):
    if f.endswith(".module.css"):
        size = os.path.getsize(os.path.join(base, "lib", f))
        print(f"  {size:>6}  {f}")

# Show a few key components' CSS
print()
print("=== Sample component styles ===")
for name in ["Button", "Input", "Menu", "Tooltip", "Toast", "Modal", "Pill", "StateDot", "MarkdownText"]:
    f = os.path.join(base, "lib", f"{name}.module.css")
    if os.path.exists(f):
        css = open(f, encoding="utf-8").read()
        # Just print the first rule
        m = re.search(r'\.([\w_]+)\s*\{([^}]+)\}', css)
        if m:
            print(f"  {name}.{m.group(1)}: {m.group(2)[:200].strip()}")
