$paths = @(
    'C:\Program Files',
    'C:\Program Files (x86)',
    'C:\Users\user\AppData\Local',
    'C:\Users\user\AppData\Roaming',
    'C:\Users\user\AppData\Roaming\Microsoft\Windows\Start Menu\Programs',
    'C:\Users\user\Desktop'
)
$found = @()
foreach ($p in $paths) {
    if (Test-Path $p) {
        $items = Get-ChildItem -Path $p -Directory -ErrorAction SilentlyContinue |
                 Where-Object { $_.Name -match 'DSH|dsh|DeepSeek|豆包|Doubao|Lingma|Trae|Cursor|Windsurf' }
        foreach ($i in $items) { $found += $i.FullName }
    }
}
$found | Sort-Object -Unique | ForEach-Object { Write-Host $_ }
