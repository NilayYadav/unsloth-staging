$ErrorActionPreference = 'Continue'
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class W {
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
  [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Ansi)]
  public struct DEVMODE {
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string dmDeviceName;
    public short dmSpecVersion, dmDriverVersion, dmSize, dmDriverExtra; public int dmFields;
    public int dmPositionX, dmPositionY, dmDisplayOrientation, dmDisplayFixedOutput;
    public short dmColor, dmDuplex, dmYResolution, dmTTOption, dmCollate;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string dmFormName;
    public short dmLogPixels; public int dmBitsPerPel, dmPelsWidth, dmPelsHeight, dmDisplayFlags, dmDisplayFrequency;
    public int dmICMMethod, dmICMIntent, dmMediaType, dmDitherType, dmReserved1, dmReserved2, dmPanningWidth, dmPanningHeight;
  }
  public delegate bool EnumProc(IntPtr h, IntPtr p);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc f, IntPtr p);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr a, int x, int y, int w, int hh, uint f);
  [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
  [DllImport("user32.dll", CharSet = CharSet.Ansi)] public static extern int ChangeDisplaySettings(ref DEVMODE d, int f);
  [DllImport("user32.dll", CharSet = CharSet.Ansi)] public static extern bool EnumDisplaySettings(string n, int m, ref DEVMODE d);
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
}
"@
[W]::SetProcessDPIAware() | Out-Null
$cfg = Join-Path $env:APPDATA 'ai.unsloth.studio'
$state = Join-Path $cfg '.window-state.json'
$failed = @()

function Set-Res($w, $h) {
  $d = New-Object W+DEVMODE
  $d.dmSize = [Runtime.InteropServices.Marshal]::SizeOf($d)
  [W]::EnumDisplaySettings($null, -1, [ref]$d) | Out-Null
  $d.dmPelsWidth = $w; $d.dmPelsHeight = $h; $d.dmFields = 0x80000 -bor 0x100000
  $rc = [W]::ChangeDisplaySettings([ref]$d, 0)
  Start-Sleep 3
  $b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
  Write-Host "ChangeDisplaySettings ${w}x${h} rc=$rc -> screen $($b.Width)x$($b.Height)"
  return ($b.Width -eq $w -and $b.Height -eq $h)
}

function Get-AppWindows {
  $pids = @(Get-Process | Where-Object { $_.Path -eq $env:APP_EXE } | ForEach-Object { $_.Id })
  $script:found = @()
  $cb = [W+EnumProc] {
    param($h, $p)
    $wp = 0; [W]::GetWindowThreadProcessId($h, [ref]$wp) | Out-Null
    if ($pids -contains $wp -and [W]::IsWindowVisible($h)) {
      $r = New-Object W+RECT; [W]::GetWindowRect($h, [ref]$r) | Out-Null
      if (($r.R - $r.L) -gt 300) { $script:found += [pscustomobject]@{ H = $h; L = $r.L; T = $r.T; R = $r.R; B = $r.B } }
    }
    return $true
  }
  [W]::EnumWindows($cb, [IntPtr]::Zero) | Out-Null
  return $script:found
}

function Shot($name) {
  $b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
  $bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size)
  $bmp.Save((Join-Path (Resolve-Path out) "$name.png")); $g.Dispose(); $bmp.Dispose()
}

function Launch-WaitApp {
  Start-Process -FilePath $env:APP_EXE | Out-Null
  $last = $null
  for ($i = 0; $i -lt 240; $i++) {
    Start-Sleep 1
    $w = @(Get-AppWindows) | Select-Object -First 1
    if ($w) {
      $sig = "$($w.L),$($w.T),$($w.R),$($w.B)"
      $width = $w.R - $w.L
      if ($width -gt 800 -and $sig -eq $last) { $stable++ } else { $stable = 0 }
      $last = $sig
      if ($stable -ge 10) { return $w }
    }
  }
  return @(Get-AppWindows) | Select-Object -First 1
}

function Quit-App($w) {
  if ($w) { [W]::PostMessage($w.H, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null }
  Start-Sleep 5
  Get-Process | Where-Object { $_.Path -eq $env:APP_EXE } | Stop-Process -Force -ErrorAction SilentlyContinue
  Start-Sleep 3
}

function Report($label, $w) {
  $wa = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
  Shot $label
  if (-not $w) { Write-Host "::error::$label no app window"; $script:failed += "${label}:nowindow"; return }
  $offR = [Math]::Max(0, $w.R - $wa.Right); $offB = [Math]::Max(0, $w.B - $wa.Bottom)
  $offL = [Math]::Max(0, $wa.Left - $w.L); $offT = [Math]::Max(0, $wa.Top - $w.T)
  $msg = "$label window L=$($w.L) T=$($w.T) R=$($w.R) B=$($w.B) ($($w.R-$w.L)x$($w.B-$w.T)); work area $($wa.Width)x$($wa.Height); off-screen px: right=$offR bottom=$offB left=$offL top=$offT"
  Write-Host $msg; Add-Content out/result.txt $msg
  if ($offR -gt 16 -or $offB -gt 16 -or $offL -gt 16 -or $offT -gt 16) { Write-Host "::error::$label window starts off screen"; $script:failed += $label }
  if (Test-Path $state) { Add-Content out/result.txt (Get-Content $state -Raw) }
}

# First launch: let the app reach full app mode once so its layout marker is written.
$w = Launch-WaitApp
Report 'first-launch' $w
Quit-App $w
Get-ChildItem $cfg -Force | ForEach-Object { Add-Content out/result.txt "cfg: $($_.Name) = $((Get-Content $_.FullName -Raw -ErrorAction SilentlyContinue))" }

# Leg A: real resolution change, as in the report.
if (Set-Res 1920 1080) {
  $w = Launch-WaitApp
  if ($w) {
    [W]::SetWindowPos($w.H, [IntPtr]::Zero, 700, 350, 1150, 680, 0x0014) | Out-Null
    Start-Sleep 3
    Report 'A1-big-screen-1920x1080' (@(Get-AppWindows) | Select-Object -First 1)
  }
  Quit-App $w
  Add-Content out/result.txt "saved after big screen: $(Get-Content $state -Raw)"
  if (Set-Res 1024 768) {
    $w = Launch-WaitApp
    Report 'A2-relaunch-after-shrink-1024x768' $w
    Quit-App $w
  } else { Write-Host '::warning::could not switch back to 1024x768' }
} else {
  Write-Host '::warning::runner refused 1920x1080; leg A skipped'
}

# Leg B: saved state from a 1920x1080 session, relaunched at the current resolution.
$b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
Write-Host "leg B screen $($b.Width)x$($b.Height)"
Set-Content $state '{"main":{"width":1150,"height":680,"x":700,"y":350,"prev_x":700,"prev_y":350,"maximized":false,"visible":true,"decorated":true,"fullscreen":false}}' -Encoding ascii
$w = Launch-WaitApp
Report 'B-seeded-big-screen-state' $w
Quit-App $w

if ($failed) { Write-Host "REPRODUCED: $($failed -join ', ')"; exit 1 }
Write-Host 'not reproduced'; exit 0
