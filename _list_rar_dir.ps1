$dir = 'C:\Users\user\Documents\xwechat_files\huashilin001_0284\msg\file\2026-08'
if (Test-Path -Path $dir) {
    Get-ChildItem -Path $dir -ErrorAction SilentlyContinue | Select-Object Name, Length, LastWriteTime | Format-Table -AutoSize
} else {
    Write-Host "Directory does not exist: $dir"
    # Try parent
    $parent = 'C:\Users\user\Documents\xwechat_files\huashilin001_0284\msg\file\2026-08'
    $pp = Split-Path $parent -Parent
    Write-Host "Trying parent: $pp"
    if (Test-Path -Path $pp) {
        Get-ChildItem -Path $pp | Select-Object Name | Format-Table -AutoSize
    } else {
        Write-Host "Parent also does not exist"
        # Try going up further
        $pp2 = Split-Path $pp -Parent
        Write-Host "Trying grandparent: $pp2"
        if (Test-Path -Path $pp2) {
            Get-ChildItem -Path $pp2 -Depth 2 -ErrorAction SilentlyContinue | Select-Object FullName | Format-Table -AutoSize
        }
    }
}
