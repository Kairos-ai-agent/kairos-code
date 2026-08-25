$root = 'C:\Program Files\DSH Desktop'
if (Test-Path $root) {
    Get-ChildItem -Path $root | Select-Object Name, Length, LastWriteTime | Format-Table -AutoSize
    Write-Host "---"
    $rd = 'C:\Users\user\AppData\Roaming\DSH Desktop'
    if (Test-Path $rd) {
        Get-ChildItem -Path $rd | Select-Object Name, LastWriteTime | Format-Table -AutoSize
    }
}
