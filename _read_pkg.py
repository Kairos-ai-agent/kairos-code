import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
pkg = r"D:\software_bak\Kairos_code\_leila_extract\8-05codex5.6产品\Leila-Codex-Offline-1.0.7-windows-python38-314-AC\.leila-ui-original-20260805-1605\package.json"
print(open(pkg, encoding="utf-8").read())
print("---")
preload = pkg.replace("package.json", "preload.js")
print(open(preload, encoding="utf-8").read())
