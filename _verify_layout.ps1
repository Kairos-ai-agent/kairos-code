Get-ChildItem 'D:\software_bak\Kairos_code\web\src' -Recurse -File -Filter '*.ts*' |
    Where-Object { $_.FullName -notmatch 'node_modules' } |
    Select-Object FullName, Length |
    Format-Table -AutoSize -Wrap
