Get-ChildItem 'D:\software_bak\Kairos_code\web' -Filter '*.test.*' -Recurse -ErrorAction SilentlyContinue | Select FullName
Write-Host "---"
Get-ChildItem 'D:\software_bak\Kairos_code\web' -Filter 'vitest*' -Recurse -ErrorAction SilentlyContinue | Select FullName
Write-Host "---"
Get-ChildItem 'D:\software_bak\Kairos_code\web' -Filter 'jest*' -Recurse -ErrorAction SilentlyContinue | Select FullName
