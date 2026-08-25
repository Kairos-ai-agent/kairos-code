"""List the .unpacked dir to find the renderer."""
import os
root = r"C:\Program Files\DSH Desktop\resources\app.asar.unpacked"
n = 0
for dirpath, dirs, files in os.walk(root):
    for f in files:
        full = os.path.join(dirpath, f)
        try:
            size = os.path.getsize(full)
        except OSError:
            size = -1
        rel = os.path.relpath(full, root)
        print(f"{size:>10}  {rel}")
        n += 1
        if n >= 200:
            break
    if n >= 200:
        break
print(f"--- total shown: {n}")
