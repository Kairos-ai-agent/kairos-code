$rar = 'C:\Users\user\Documents\xwechat_files\huashilin001_0284\msg\file\2026-08\8-05codex5.6产品.rar'
$unrar = 'C:\Program Files\WinRAR\UnRAR.exe'
# List contents (top-level only)
& $unrar l -slt $rar 2>&1 | Select-Object -First 100
