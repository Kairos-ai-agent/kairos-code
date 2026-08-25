"""Find the renderer/UI files in the extracted DSH app."""
import os
out = r"D:\software_bak\Kairos_code\_dsh_extract"

# Look for HTML, CSS, and the main entry
candidates = []
for dirpath, dirs, files in os.walk(out):
    for f in files:
        ext = os.path.splitext(f)[1].lower()
        if ext in (".html", ".css", ".svg"):
            full = os.path.join(dirpath, f)
            try:
                size = os.path.getsize(full)
            except OSError:
                size = -1
            rel = os.path.relpath(full, out)
            candidates.append((size, rel, f))

candidates.sort(reverse=True)
print("=== Top 50 HTML/CSS/SVG files by size ===")
for size, rel, f in candidates[:50]:
    print(f"{size:>10}  {rel}")

print()
print("=== package.json main + renderer hints ===")
import json
for dirpath, dirs, files in os.walk(out):
    if "package.json" in files:
        pj = os.path.join(dirpath, "package.json")
        try:
            data = json.loads(open(pj, encoding="utf-8").read())
        except Exception:
            continue
        rel = os.path.relpath(pj, out)
        if "main" in data or "name" in data:
            print(f"{rel}: main={data.get('main')}, name={data.get('name')}")
