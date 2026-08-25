$unpacked = 'C:\Program Files\DSH Desktop\resources\app.asar.unpacked'
if (Test-Path $unpacked) {
    Get-ChildItem -Path $unpacked -Recurse -ErrorAction SilentlyContinue |
        Select-Object FullName, Length |
        Format-Table -AutoSize
}
