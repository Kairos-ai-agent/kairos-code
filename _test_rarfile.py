try:
    import rarfile
    print("rarfile available, version:", rarfile.__version__)
except ImportError as e:
    print("rarfile NOT installed:", e)

# Check if UnRAR DLL is findable
import os
candidates = [
    r"C:\Program Files\WinRAR\UnRAR.exe",
    r"C:\Program Files (x86)\WinRAR\UnRAR.exe",
    r"C:\Windows\System32\UnRAR.dll",
    r"C:\Program Files\7-Zip\7z.exe",
]
for c in candidates:
    print(c, "->", "EXISTS" if os.path.exists(c) else "missing")
