$p = 'D:\software_bak\Kairos_code\api\routes\projects.py'
$c = Get-Content $p -Raw
$c = $c -replace 'orchestrator\.', '_orch().'
Set-Content -Path $p -Value $c -Encoding UTF8
Write-Host "done"
