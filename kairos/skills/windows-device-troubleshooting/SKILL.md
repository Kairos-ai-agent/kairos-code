---
name: "windows-device-troubleshooting"
description: "Diagnose and fix Windows hardware device issues — printers, USB peripherals, and other connected devices showing offline, not detected, or not working. Covers PowerShell diagnostics, WMI queries, driv"
priority: 0.5
imported-from: "agents"
source-path: "agents/skills/software-development/windows-device-troubleshooting/SKILL.md"
---
# Windows Device Troubleshooting

Diagnose hardware issues on Windows via PowerShell. The key insight: **software status lying** — Windows may show a printer as "Normal" while the hardware is not actually detected.

## Diagnostic Flow (order matters)

### Phase 1: Software Status
Check what Windows *thinks* the device status is.

```powershell
# Printer overview — status, offline flag, port, driver
Get-Printer | Select-Object Name, PortName, PrinterStatus, DriverName | Format-Table -AutoSize

# Deeper: WMI view with WorkOffline flag
Get-CimInstance -Class Win32_Printer | Select-Object Name, WorkOffline, PrinterStatus, PortName | Format-Table -AutoSize

# Check for stuck print jobs
Get-CimInstance -Class Win32_PrintJob | Select-Object PrinterName, JobStatus, DocumentName | Format-Table -AutoSize
```

**Pitfall:** `PrinterStatus = 3` (Normal) does NOT mean the printer is reachable. Always check `WorkOffline` and then verify hardware detection in Phase 2.

### Phase 2: Hardware Detection
Verify Windows actually sees the physical device.

```powershell
# Check if printer appears as a PnP device
Get-CimInstance -Class Win32_PnPEntity | Where-Object { $_.Name -match 'print|pantum|deli|hp|canon|epson|brother' } | Select-Object Name, Status, ConfigManagerErrorCode | Format-Table -AutoSize

# List all USB-connected devices
Get-CimInstance -Class Win32_PnPEntity | Where-Object { $_.DeviceID -like 'USB*' } | Select-Object Name, Status, DeviceID | Format-Table -AutoSize

# USB controller devices (includes hubs, peripherals)
Get-CimInstance -Class Win32_USBControllerDevice | ForEach-Object { [wmi]$_.Dependent } | Select-Object Name, Status | Format-Table -AutoSize
```

**Pitfall:** If the printer is NOT in the Win32_PnPEntity list, it is a **hardware/connection issue**, not a driver or software issue. No amount of driver reinstall or spooler restart will help.

### Phase 3: Fix — Software Side
Only proceed if Phase 2 confirmed hardware is detected.

```powershell
# Set offline printers back to online
Get-CimInstance -Class Win32_Printer | Where-Object { $_.WorkOffline -eq $true } | ForEach-Object {
    $_.WorkOffline = $false
    Set-CimInstance -InputObject $_
    Write-Host "Set online: $($_.Name)"
}

# Clear stuck print queue
Get-CimInstance -Class Win32_PrintJob | Remove-CimInstance -ErrorAction SilentlyContinue

# Restart print spooler
Restart-Service -Name Spooler -Force
```

**Pitfall:** `Restart-Service Spooler` may fail with "cannot stop service" if print jobs are actively processing. Clear jobs first, then restart.

### Printer Services Reference

Required services for printing:

| Service | StartType | Notes |
|---------|-----------|-------|
| **Spooler** (Print Spooler) | Automatic | Must be Running |
| **PrintDeviceConfigurationService** | Manual | Should be Running |
| **PrintNotify** | Manual | Should be Running |
| **PrintScanBrokerService** | Manual | Should be Running |

Start services (needs admin):

```powershell
# Start Spooler normally
Start-Service -Name Spooler
Set-Service -Name Spooler -StartupType Automatic

# Start other services with admin elevation
Start-Process powershell -Verb RunAs -ArgumentList '-Command', 'Start-Service -Name PrintDeviceConfigurationService; Start-Service -Name PrintNotify; Start-Service -Name PrintScanBrokerService' -Wait -WindowStyle Hidden
```

### Clear Stuck Print Queue (Alternative — Command Line)

```powershell
net stop spooler
del /Q /F /S "%systemroot%\System32\spool\PRINTERS\*.*"
net start spooler
```

### Check Drivers and Ports

```powershell
# Check installed printer drivers
Get-PrinterDriver | Select-Object Name, Manufacturer, PrinterEnvironment, IsSigned | Format-Table -AutoSize

# Check port configuration (USB printers should show USB001, USB002)
Get-PrinterPort | Select-Object Name, PortMonitor, HostAddress, PortNumber | Format-Table -AutoSize
```

### Phase 4: Fix — Hardware Side
If Phase 2 showed device not detected:

1. **Physical connection:** Unplug/replug USB cable (both ends), try different USB port (rear ports have better power)
2. **Cable:** Swap USB cable (cables go bad)
3. **Printer power:** Confirm printer is ON with no error lights/messages on panel
4. **USB mode:** Some printers have a setting to switch between USB/network/WiFi modes
5. **Cross-test:** Connect printer to a different PC to rule out dead USB port on printer

## Common WMI Classes Reference

| Class | Purpose |
|-------|---------|
| `Win32_Printer` | Installed printers, status, ports, offline flag |
| `Win32_PrintJob` | Active/queued print jobs |
| `Win32_PnPEntity` | All Plug-and-Play devices (the "device manager" view) |
| `Win32_USBControllerDevice` | USB device-to-controller mapping |
| `Win32_TCPIPPrinterPort` | Network printer port config |

## Pitfalls

- `Get-Printer` shows installed printer objects (can exist without hardware). `Win32_PnPEntity` shows detected hardware. Both must agree for printing to work.
- PowerShell error `Get-PrintJob : 指定的服务器不存在` with `-PrinterName *` — use `Get-CimInstance -Class Win32_PrintJob` instead.
- Chinese Windows may garble PowerShell output in git-bash (MSYS). Output still works, just looks like mojibake in the terminal — the commands execute correctly.
- WMI queries with `[wmi]$_.Dependent` casting may fail for some USB devices. Not all PnP entities are castable to ManagementObject. Use `Get-CimInstance` directly instead.
- **Admin required for service changes**: Use `Start-Process powershell -Verb RunAs` to elevate. `Set-Service` will fail with "PermissionDenied" without elevation.
- **Multiple copies of same printer**: Windows may create duplicate printer entries (e.g., "Deli DL-730C(NEW) (副本 1)"). Check which port each uses.
- **WorkOffline vs not detected**: Printer showing "offline" in settings AND not appearing in USB device list = hardware connection issue, not software. Setting WorkOffline=False won't help if Windows can't see the device.
