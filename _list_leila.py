import os
root = r"D:\software_bak\Kairos_code\_leila_extract"
for dirpath, dirs, files in os.walk(root):
    for f in files:
        full = os.path.join(dirpath, f)
        size = os.path.getsize(full)
        rel = os.path.relpath(full, root)
        print(f"{size:>10}  {rel}")
