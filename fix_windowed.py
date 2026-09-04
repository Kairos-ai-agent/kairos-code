"""Add --windowed to build_exe_with_icon.py for windowed EXE (no console)."""
import os
p = r"D:\software_bak\Kairos_code\build_exe_with_icon.py"
t = open(p, encoding="utf-8").read()

old = '"--noconfirm", "--onefile", "--name", "kairos-code",\n        "--icon", str(ICON),'
new = ('"--noconfirm", "--onefile", "--name", "kairos-code",\n'
       '        "--icon", str(ICON),\n'
       '        "--windowed",  # GUI app: no black console window')

if "--windowed" not in t:
    t = t.replace(old, new, 1)
    open(p, "w", encoding="utf-8").write(t)
    print("added --windowed")
else:
    print("already")
