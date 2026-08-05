Set WshShell = CreateObject("WScript.Shell")

' Kill by port
WshShell.Run "cmd /c for /f ""tokens=5"" %a in ('netstat -aon ^| findstr "":8900.*LISTENING""') do taskkill /PID %a /F", 0, True
WshShell.Run "cmd /c for /f ""tokens=5"" %a in ('netstat -aon ^| findstr "":3000.*LISTENING""') do taskkill /PID %a /F", 0, True

MsgBox "Kairos Code stopped.", vbInformation, "Kairos Code"
