@echo off
REM Build EmployeeVisitTracker.exe with logo icon
REM Requires: pip install -r requirements.txt
setlocal
cd /d "%~dp0"

if not exist logo.png (
  echo ERROR: logo.png not found in %~dp0
  exit /b 1
)

echo ==^> Generating logo.ico from logo.png...
py -3 -c "from PIL import Image; import os; src='logo.png'; im=Image.open(src).convert('RGBA'); s=max(im.size); bg=Image.new('RGBA',(s,s),(255,255,255,0)); bg.paste(im, ((s-im.size[0])//2,(s-im.size[1])//2), im); bg.save('logo.ico', format='ICO', sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)]); print('logo.ico created:', os.path.getsize('logo.ico'))"
if not exist logo.ico (
  echo ERROR: logo.ico generation failed
  exit /b 1
)

echo ==^> Building exe with PyInstaller...
py -3 -m PyInstaller EmployeeVisitTracker.spec --clean --noconfirm --log-level WARN
if errorlevel 1 (
  echo ERROR: PyInstaller failed
  exit /b 1
)

echo ==^> Done. Output: dist\EmployeeVisitTracker.exe
dir dist\EmployeeVisitTracker.exe
echo Run: dist\EmployeeVisitTracker.exe
