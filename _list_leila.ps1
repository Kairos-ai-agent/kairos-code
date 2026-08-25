$root = 'D:\software_bak\Kairos_code\_leila_extract'
if (Test-Path $root) {
    Get-ChildItem -Path $root -Recurse -File -ErrorAction SilentlyContinue |
        Select-Object FullName, Length |
        Format-Table -AutoSize
} else {
    Write-Host "Missing: $root"
}
