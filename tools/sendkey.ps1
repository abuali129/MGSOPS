param([string]$Keys = "{F2}")
# Send keystrokes to the PPSSPP window (F2 = save state, F4 = load state).
Add-Type -AssemblyName System.Windows.Forms
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class FW { [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h); }
"@
$p = Get-Process PPSSPPWindows64 -ErrorAction Stop | Select-Object -First 1
[FW]::SetForegroundWindow($p.MainWindowHandle) | Out-Null
Start-Sleep -Milliseconds 300
[System.Windows.Forms.SendKeys]::SendWait($Keys)
"sent $Keys"
