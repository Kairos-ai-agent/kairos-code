---
name: "windows-printer-troubleshooting"
description: "Diagnose and fix Windows printer issues — offline status, USB detection failures, stopped print services, driver problems. Use when the user reports printer not working, showing offline, not connectin"
priority: 0.5
imported-from: "hermes"
source-path: "hermes/skills/.archive/windows-printer-troubleshooting/SKILL.md"
---
# Windows Printer Troubleshooting

Systematic diagnosis for printer issues on Windows. Always check in this order — each step catches a different class of failure.

## Diagnosis Order

1. **Printer online/offline status** — may be software-set to offline
2. **USB device detection** — is Windows seeing the hardware at all?
3. **Print services** — are the required Windows services running?
4. **Driver installation** — is the driver present?
5. **Port configuration** — are ports correctly bound?

## Step 1: Check Printer Status

```powershell
# Check WorkOffline flag and printer status
Get-CimInstance -Class Win32_Printer | Select-Object Name, WorkOffline, PrinterStatus, PortName | Format-Table -AutoSize

# PrinterStatus values: 2=Idle, 3=Printing, 4=Error
```

If `WorkOffline` is `True`, set to online:

```powershell
Get-CimInstance -Class Win32_Printer | Where-Object { $_.WorkOffline -eq $true } | ForEach-Object {
    $_.WorkOffline = $false
    Set-CimInstance -InputObject $_
}
```

## Step 2: Check USB Device Detection

**Critical check** — if printers don't appear here, Windows isn't seeing the hardware at all.

```powershell
# List all USB devices
Get-CimInstance -Class Win32_PnPEntity | Where-Object { $_.DeviceID -like 'USB*' } | Select-Object Name, Status | Format-Table -AutoSize

# Search specifically for printer hardware
Get-CimInstance -Class Win32_PnPEntity | Where-Object { $_.Name -match 'print|pantum|deli|hp|canon|epson|brother' } | Select-Object Name, Status, ConfigManagerErrorCode | Format-Table -AutoSize

# Check for devices with errors (ConfigManagerErrorCode != 0)
Get-CimInstance -Class Win32_PnPEntity | Where-Object { $_.ConfigManagerErrorCode -ne 0 } | Select-Object Name, Status, ConfigManagerErrorCode | Format-Table -AutoSize
```

If printer hardware NOT detected:
- Replug USB cable (both ends)
- Try different USB port (rear ports have better power)
- Try a different USB cable
- Test printer on another computer to rule out hardware failure

## Step 3: Check and Start Print Services

```powershell
# Check all print-related services
Get-Service -Name '*print*', Spooler | Select-Object Name, Status, StartType | Format-Table -AutoSize
```

Required services:
- **Spooler** (Print Spooler) — must be Running, Automatic
- **PrintDeviceConfigurationService** — Manual, should be Running
- **PrintNotify** — Manual, should be Running
- **PrintScanBrokerService** — Manual, should be Running

Start services (needs admin):

```powershell
# Start Spooler normally
Start-Service -Name Spooler
Set-Service -Name Spooler -StartupType Automatic

# Start other services with admin elevation
Start-Process powershell -Verb RunAs -ArgumentList '-Command', 'Start-Service -Name PrintDeviceConfigurationService; Start-Service -Name PrintNotify; Start-Service -Name PrintScanBrokerService' -Wait -WindowStyle Hidden
```

## Step 4: Check Drivers

```powershell
Get-PrinterDriver | Select-Object Name, Manufacturer, PrinterEnvironment, IsSigned | Format-Table -AutoSize
```

If driver missing, download from manufacturer website and install.

## Step 5: Check Port Configuration

```powershell
Get-PrinterPort | Select-Object Name, PortMonitor, HostAddress, PortNumber | Format-Table -AutoSize
```

USB printers should show ports like `USB001`, `USB002` with `Dynamic Print Monitor`.

## Clear Stuck Print Queue

```powershell
# Remove all pending jobs
Get-CimInstance -Class Win32_PrintJob | Remove-CimInstance -ErrorAction SilentlyContinue

# Or via command line (admin)
net stop spooler
del /Q /F /S "%systemroot%\System32\spool\PRINTERS\*.*"
net start spooler
```

## Pitfalls

- **git-bash/MSYS encoding**: PowerShell output with Chinese characters gets garbled in bash. Use `powershell -Command` directly, don't pipe through bash.
- **Admin required for service changes**: Use `Start-Process powershell -Verb RunAs` to elevate. `Set-Service` will fail with "PermissionDenied" without elevation.
- **WorkOffline vs not detected**: Printer showing "offline" in settings AND not appearing in USB device list = hardware connection issue, not software. Setting WorkOffline=False won't help if Windows can't see the device.
- **Multiple copies of same printer**: Windows may create duplicate printer entries (e.g., "Deli DL-730C(NEW) (副本 1)"). Check which port each uses.
