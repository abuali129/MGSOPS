param([string]$Dir = "frames", [double]$Seconds = 20)
# Capture the PPSSPP window repeatedly (as fast as PrintWindow allows) into $Dir\f000.jpg ...
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
New-Item -ItemType Directory -Force $Dir | Out-Null
$p = Get-Process PPSSPPWindows64 -ErrorAction Stop | Select-Object -First 1
$h = $p.MainWindowHandle
$r = New-Object W+RECT
[W]::GetClientRect($h, [ref]$r) | Out-Null
$w = ($r.R - $r.L) / 2; $ht = ($r.B - $r.T) / 2
$sw = [Diagnostics.Stopwatch]::StartNew(); $n = 0
while ($sw.Elapsed.TotalSeconds -lt $Seconds) {
  $bmp = New-Object System.Drawing.Bitmap ($r.R - $r.L), ($r.B - $r.T)
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $hdc = $g.GetHdc(); [W]::PrintWindow($h, $hdc, 3) | Out-Null; $g.ReleaseHdc($hdc); $g.Dispose()
  $small = New-Object System.Drawing.Bitmap $bmp, $w, $ht
  $small.Save((Join-Path $Dir ("f{0:D3}.jpg" -f $n)), [System.Drawing.Imaging.ImageFormat]::Jpeg)
  $small.Dispose(); $bmp.Dispose(); $n++
}
"$n frames in $([math]::Round($sw.Elapsed.TotalSeconds,1)) s"
