# Try to find asar tools
$tools = @('npx','asar','node')
foreach ($t in $tools) {
    $c = Get-Command $t -ErrorAction SilentlyContinue
    if ($c) { Write-Host "$t : $($c.Source)" } else { Write-Host "$t : NOT FOUND" }
}
# Check if any local node_modules has asar
$nm = 'C:\Program Files\DSH Desktop\resources\app.asar.unpacked\node_modules'
if (Test-Path $nm) {
    Get-ChildItem $nm -Filter '*asar*' -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "package: $($_.FullName)" }
}
