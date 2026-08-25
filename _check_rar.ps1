$path = 'C:\Users\user\Documents\xwechat_files\huashilin001_0284\msg\file\2026-08\8-05codex5.6产品.rar'
Write-Host "Exists: $(Test-Path -Path $path -PathType Leaf)"
if (Test-Path -Path $path) {
    $info = Get-Item -Path $path
    Write-Host "Size: $($info.Length) bytes"
    Write-Host "LastWriteTime: $($info.LastWriteTime)"
}
