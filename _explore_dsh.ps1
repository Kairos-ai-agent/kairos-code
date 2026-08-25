$root = 'C:\Program Files\DSH Desktop'
Get-ChildItem -Path $root\resources -ErrorAction SilentlyContinue |
    Select-Object Name, Length | Format-Table -AutoSize
Write-Host "---app dir---"
if (Test-Path "$root\resources\app") {
    Get-ChildItem -Path "$root\resources\app" | Select-Object Name | Format-Table -AutoSize
}
Write-Host "---asar present?---"
Get-ChildItem -Path $root\resources -Filter '*.asar' -ErrorAction SilentlyContinue |
    Select-Object Name, Length | Format-Table -AutoSize
