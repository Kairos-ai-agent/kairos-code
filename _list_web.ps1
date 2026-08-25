Get-ChildItem 'D:\software_bak\Kairos_code\web' -Recurse -File -ErrorAction SilentlyContinue |
    Select-Object FullName, Length |
    Format-Table -AutoSize -Wrap
