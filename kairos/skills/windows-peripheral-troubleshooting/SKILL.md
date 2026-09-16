---
name: "windows-peripheral-troubleshooting"
description: ">-"
priority: 0.5
imported-from: "hermes"
source-path: "hermes/skills/.archive/windows-peripheral-troubleshooting/SKILL.md"
---
# Windows Peripheral Troubleshooting

Use when the user reports a Windows peripheral as **offline**, **not responding**, **not detected**, **yellow bang in Device Manager**, or **works sometimes**:

- Printers (USB or networked, showing "Offline" in Windows Settings)
- USB drives / external SSDs that intermittently disappear
- Webcams, scanners, USB audio devices
- Network adapters, Bluetooth adapters

Trigger phrases (Chinese): "打印机脱机", "USB 设备不识别", "设备管理器黄色感叹号", "X 设备不见了", "X 突然连不上".

## Core principle: PnP is the source of truth, high-level APIs lie

A printer can report `PrinterStatus: Normal` via `Get-Printer` while its underlying USB PnP device is in `CM_PROB_PHANTOM`. **Always cross-check at the PnP layer before trusting any high-level status**.

```powershell
# High-level (can lie about Normal even when device is dead)
Get-Printer | Where-Object { $_.Name -match 'Pantum' } | Format-List Name, PrinterStatus, WorkOffline

# Low-level (source of truth)
Get-PnpDevice | Where-Object { $_.InstanceId -match 'VID_232B' } | Format-Table Status, FriendlyName, Problem, InstanceId
```

Vendor VID lookup: Pantum=`VID_232B`, Canon=`VID_04A9`, HP=`VID_03F0`, Brother=`VID_04F9`, Epson=`VID_04B8`, generic USB printers often `VID_1A86` or `VID_1A19`. For non-printer peripherals, drop the VID filter and pipe `Get-PnpDevice -Class USB` (or `-Class Bluetooth`, `-Class Net`, etc.) into the same view.

## Common CM_PROB_* codes and what they mean

| Code | Name | Meaning | Fix |
|---|---|---|---|
| 1 (`CM_PROB_NOT_CONFIGURED`) | not configured | Device present but not set up | Right-click → Properties → reconfigure, or reinstall driver |
| 10 (`CM_PROB_FAILED_START`) | cannot start | Driver failed to load | Disable/enable device; reinstall driver; check WinUsb/USB class driver |
| 28 (`CM_PROB_FAILED_INSTALL`) | driver not installed | Driver package missing | Install driver from manufacturer or Windows Update |
| **45 (`CM_PROB_PHANTOM`)** | **was once here, now gone** | **Device enumerated in registry but USB bus no longer responds — physical layer dead** | **Software CANNOT fix. Require physical action: cable reseat → power cycle → port swap → driver uninstall + restart** |
| Code 52 (`CM_PROB_LIAR`) | driver block | Driver blocked by policy | Check `DisableDevice` registry key or driver signature enforcement |
| Code 43 (`CM_PROB_HALTED`) | device stopped | USB hub stopped the device (often power or descriptor error) | Try different USB port (especially rear panel, not hub); check USB cable |

The key diagnostic code: **`CM_PROB_PHANTOM` = physical USB gone**. If you see this, stop trying software fixes and ask the user to physically reseat/repower the device. Wasting time on `Disable-PnpDevice` / `Enable-PnpDevice` / `pnputil /scan-devices` against a phantom device produces no change in PnP status — those commands re-trigger enumeration, but enumeration already happened, the device just isn't there.

## The Hermes admin boundary

Hermes's terminal tool runs **unprivileged** by default on Windows (verified via `[Security.Principal.WindowsPrincipal]::IsInRole(Administrator)` → `False`). Several diagnostic commands silently fail without admin:

- `pnputil /scan-devices` → returns `拒绝访问 / Access is denied`
- `Disable-PnpDevice`, `Enable-PnpDevice` → throw "Access is denied" or hang silently
- `Restart-Service Spooler -Force` → fails
- Anything in `HKLM` registry → fails

**To run admin commands, you must escalate via UAC — and you can't bypass it.** Pattern:

```powershell
# 1. Write the script to TEMP so the elevated process can find it
$tmp = Join-Path $env:TEMP 'fix.ps1'
Set-Content -Path $tmp -Value $script -Encoding UTF8

# 2. Launch elevated via UAC (user must click "Yes" in the prompt)
$proc = Start-Process -FilePath 'powershell.exe' `
    -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File',$tmp `
    -Verb RunAs -PassThru -WindowStyle Hidden

# 3. CRITICAL: capture stdout/stderr via a log file inside the script,
#    and check the file after WaitForExit. This is your side channel —
#    if the log file doesn't exist, UAC was rejected (user clicked No)
#    or the script crashed before writing.
$proc.WaitForExit(45) | Out-Null
Test-Path (Join-Path $env:TEMP 'fix.log')   # → False means UAC rejected
```

**Pitfall**: `Start-Process -Verb RunAs` returns a `$proc` even when UAC is rejected, and `WaitForExit` completes with an empty ExitCode. You cannot tell UAC was rejected from `$proc` alone. **Always verify via a side-channel artifact** (log file, file modification, registry write) — never trust the process object.

**Pitfall**: If UAC is silent-rejected (admin policy), `Start-Process -Verb RunAs` throws `Win32Exception` immediately. Catch it and fall through to telling the user to run as admin manually.

## Physical fallback boundary

When PnP says `CM_PROB_PHANTOM` AND admin escalation didn't help AND you've already tried `Disable-PnpDevice` + `Enable-PnpDevice` once, **stop trying software fixes**. The fix path is fixed:

1. Reseat the USB cable at both ends (5 second unplug)
2. Power-cycle the device (full off, 30 seconds, on)
3. Try a different USB cable
4. Try a different USB port (prefer rear-panel directly on motherboard, avoid front-panel headers and unpowered hubs)
5. If still phantom: Device Manager → uninstall the phantom device → physically unplug → restart Windows → reconnect

You do not need to enumerate all 5 steps. Give the user the cheapest one first ("unplug USB, wait 5 seconds, plug back in") and re-check PnP state. Only escalate if it fails.

## Diagnostic command sequence (run these in order)

```powershell
# 1. Identify the device + check service state
Get-Service Spooler | Select-Object Name, Status, StartType
Get-Printer | Format-Table Name, PrinterStatus, PortName, DriverName

# 2. Cross-check at PnP layer (source of truth)
Get-PnpDevice | Where-Object { $_.InstanceId -match 'VID_XXXX' } |
    Format-Table Status, FriendlyName, Problem, InstanceId

# 3. Check physical USB enumeration (does the USB hub see anything?)
Get-PnpDevice -Class USB | Where-Object { $_.Status -ne 'OK' } |
    Format-Table Status, FriendlyName, Problem

# 4. Re-check after user performs physical action
# (same commands as 1+2)
```

If step 2 shows `Status=OK, Problem=No` but the device is non-functional, the problem is at a higher layer (driver logic, application config, network). The PnP layer is not your bottleneck.

If step 2 shows `CM_PROB_PHANTOM`, the problem is physical and cannot be fixed in software.

## Pitfalls

- Don't trust `Get-Printer` PrinterStatus / Windows Settings → cross-check with `Get-PnpDevice`
- Don't keep retrying software fixes after seeing `CM_PROB_PHANTOM` — it's a hardware signal-loss code
- Don't assume `Start-Process -Verb RunAs` succeeded just because `$proc` is non-null — verify via a side-channel artifact (log file written by the elevated script)
- Don't try `pnputil /scan-devices` from an unprivileged terminal — it silently returns `Access denied` and you waste a round-trip thinking the command is slow
- Hermes runs unprivileged; spell out to the user when UAC will pop rather than surprising them

## References
- `references/windows-pnp-error-codes.md` — full CM_PROB_* code table with causes and fixes
- `references/usb-vendor-ids.md` — common printer/peripheral VIDs for filtering `Get-PnpDevice`