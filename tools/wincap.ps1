param([string]$Out = "shot.png")
# Capture the PPSSPP window (PrintWindow with PW_RENDERFULLCONTENT works for Vulkan/D3D surfaces).
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class W2 { [DllImport("user32.dll")] public static extern bool SetProcessDPIAware(); }
public class W {
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
  [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint f);
}
"@
[void][W2]::SetProcessDPIAware()
$p = Get-Process PPSSPPWindows64 -ErrorAction Stop | Select-Object -First 1
$h = $p.MainWindowHandle
$r = New-Object W+RECT
[W]::GetClientRect($h, [ref]$r) | Out-Null
$bmp = New-Object System.Drawing.Bitmap ($r.R - $r.L), ($r.B - $r.T)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$hdc = $g.GetHdc()
[W]::PrintWindow($h, $hdc, 3) | Out-Null   # PW_CLIENTONLY | PW_RENDERFULLCONTENT
$g.ReleaseHdc($hdc)
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
"saved $Out $($bmp.Width)x$($bmp.Height)"
