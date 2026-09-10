# Build EmployeeVisitTracker.exe with logo icon (always regenerates logo.ico)
# Usage:
#   powershell -ExecutionPolicy Bypass -File build_exe.ps1
#   or right-click -> Run with PowerShell
# Requires: pip install -r requirements.txt  (pyinstaller, pillow, etc.)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path "logo.png")) {
  Write-Error "logo.png not found in $PSScriptRoot"
  exit 1
}

Write-Host "==> Generating logo.ico from logo.png (transparent square, 6 sizes)..." -ForegroundColor Green
py -3 -c "from PIL import Image; import os, sys; src='logo.png'; im=Image.open(src).convert('RGBA'); s=max(im.size); bg=Image.new('RGBA',(s,s),(255,255,255,0)); bg.paste(im, ((s-im.size[0])//2,(s-im.size[1])//2), im); bg.save('logo.ico', format='ICO', sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)]); print('logo.ico created:', os.path.getsize('logo.ico'), 'bytes'); img=Image.open('logo.ico'); print('ICO sizes:', img.info.get('sizes'))"
if (-not (Test-Path "logo.ico")) { Write-Error "logo.ico generation failed"; exit 1 }

Write-Host "==> Building exe with PyInstaller (EmployeeVisitTracker.spec)..." -ForegroundColor Green
# spec bundles: logo.png, logo.svg, bangladesh-govt-logo.svg, fonts/, logo.ico and sets icon
py -3 -m PyInstaller EmployeeVisitTracker.spec --clean --noconfirm --log-level WARN
if ($LASTEXITCODE -ne 0) { Write-Error "PyInstaller failed with exit code $LASTEXITCODE"; exit $LASTEXITCODE }

Write-Host "==> Done." -ForegroundColor Cyan
$exe = "dist\EmployeeVisitTracker.exe"
if (Test-Path $exe) {
  Get-ChildItem $exe | Format-List Name, Length, @{N='SizeMB';E={[math]::Round($_.Length/1MB,2)}}, LastWriteTime, Directory
  Write-Host "Run: .\$exe" -ForegroundColor Yellow
  # verify icon embedded
  try {
    Add-Type -AssemblyName System.Drawing -ErrorAction SilentlyContinue
    $ico = [System.Drawing.Icon]::ExtractAssociatedIcon((Resolve-Path $exe).Path)
    if ($ico) { Write-Host "Icon verified: $($ico.Width)x$($ico.Height)" -ForegroundColor Green }
  } catch {}
} else {
  Write-Error "Build finished but $exe not found"
  exit 1
}
