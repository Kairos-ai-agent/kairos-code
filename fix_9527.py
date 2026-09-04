"""Lock port to 9527 in all 4 sites."""
import os
ROOT = r"D:\software_bak\Kairos_code"
repls = [
    # 1. vite.config.ts
    (os.path.join(ROOT, "web", "vite.config.ts"),
     "['8900', '8964', '8966']",
     "['9527']"),
    # 2. kairos_code_launcher.py
    (os.path.join(ROOT, "kairos_code_launcher.py"),
     "default=8900",
     "default=9527"),
    # 3. start_silent.bat (uses port via start.vbs / kairos.main default)
    (os.path.join(ROOT, "start_silent.bat"),
     "wscript.exe start.vbs",
     "set KAIROS_PORT=9527\r\nwscript.exe start.vbs"),
    # 4. kairos/config/settings.py
    (os.path.join(ROOT, "kairos", "config", "settings.py"),
     '"port": 8900',
     '"port": 9527'),
]
for path, old, new in repls:
    if not os.path.exists(path):
        print(f"MISSING: {path}")
        continue
    t = open(path, encoding="utf-8").read()
    if old in t:
        t = t.replace(old, new, 1)
        open(path, "w", encoding="utf-8").write(t)
        print(f"OK  {os.path.basename(path)}")
    else:
        print(f"NOT-FOUND  {os.path.basename(path)} (looking for {old!r})")
print("done")
