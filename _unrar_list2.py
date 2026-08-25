import subprocess
import sys

rar = r"D:\software_bak\Kairos_code\_codex56.rar"
unrar = r"C:\Program Files\WinRAR\UnRAR.exe"
res = subprocess.run(
    [unrar, "l", "-slt", rar],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
    timeout=120,
)
# Force UTF-8 output
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
lines = res.stdout.splitlines()
print(f"Total lines: {len(lines)}")
print("---first 200 lines---")
for line in lines[:200]:
    print(line)
