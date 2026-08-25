$tools = @('unrar.exe', '7z.exe', 'WinRAR.exe', 'unar.exe', 'bsdtar.exe')
foreach ($t in $tools) {
    $found = Get-Command $t -ErrorAction SilentlyContinue
    if ($found) {
        Write-Host "$t FOUND: $($found.Source)"
    } else {
        Write-Host "$t NOT found"
    }
}
# Also check the venv site-packages
$venv = 'D:\software_bak\Kairos_code\.venv\Lib\site-packages'
Get-ChildItem -Path $venv -Filter '*rar*' -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "package: $($_.Name)" }
