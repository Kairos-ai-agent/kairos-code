$src = 'C:\Users\user\DOCUME~1\XWECHA~1\HUASHI~1\msg\file\2026-08\8-05CO~1.RAR'
$dst = 'D:\software_bak\Kairos_code\_codex56.rar'
Write-Host "Source exists: $(Test-Path -Path $src)"
Write-Host "Source size: $((Get-Item $src).Length)"
# Use Copy-Item -Force to overwrite if it exists
Copy-Item -Path $src -Destination $dst -Force
Write-Host "Copy done. Dest exists: $(Test-Path -Path $dst)"
Write-Host "Dest size: $((Get-Item $dst).Length)"
