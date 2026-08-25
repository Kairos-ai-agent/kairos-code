$dir = 'C:\Users\user\Documents\xwechat_files\huashilin001_0284\msg\file\2026-08'
$files = Get-ChildItem -Path $dir -Filter '*.rar' -ErrorAction SilentlyContinue
foreach ($f in $files) {
    Write-Host "Found: [$($f.Name)] size=$($f.Length)"
    Write-Host "Full: [$($f.FullName)]"
}
