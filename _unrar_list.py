import subprocess
rar = r"D:\software_bak\Kairos_code\_codex56.rar"
unrar = r"C:\Program Files\WinRAR\UnRAR.exe"
# List with technical detail (-slt), limit output
res = subprocess.run(
    [unrar, "l", "-slt", rar],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
    timeout=60,
)
# Print first 80 lines
lines = res.stdout.splitlines()
for line in lines[:80]:
    print(line)
print("---")
print("Total lines:", len(lines))
print("Stderr (first 5):")
for line in res.stderr.splitlines()[:5]:
    print(line)
