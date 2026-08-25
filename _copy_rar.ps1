$dir = 'C:\Users\user\Documents\xwechat_files\huashilin001_0284\msg\file\2026-08'
$files = Get-ChildItem -Path $dir -Filter '*.rar' -ErrorAction SilentlyContinue
foreach ($f in $files) {
    # Use cmd-style short path (8.3) to avoid encoding issues
    $short = $f.FullName
    try {
        $shortPath = (New-Object -ComObject Scripting.FileSystemObject).GetFile($f.FullName).ShortPath
    } catch {
        $shortPath = $f.FullName
    }
    Write-Host "Short: $shortPath"
}
