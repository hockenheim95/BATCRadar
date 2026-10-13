@echo off
cd /d "%~dp0"
setlocal EnableDelayedExpansion

for /f "usebackq delims=" %%v in ("VERSION") do set "VER=%%v"
echo.
echo  BATCRadar Build  v%VER%
echo  -------------------
echo.

python -m pip install --disable-pip-version-check -q -r requirements.txt pyinstaller SimConnect
if errorlevel 1 (
  echo  pip/PyInstaller fehlgeschlagen. Python 3 muss installiert sein.
  pause
  exit /b 1
)

if exist build rd /s /q build
if exist dist\BATCRadar rd /s /q dist\BATCRadar
if exist "dist\BATCRadar-v%VER%.zip" del /q "dist\BATCRadar-v%VER%.zip"

python -m PyInstaller --noconfirm --clean BATCRadar.spec
if errorlevel 1 (
  echo  PyInstaller fehlgeschlagen.
  pause
  exit /b 1
)

if not exist dist mkdir dist
powershell -NoProfile -Command "Compress-Archive -Path 'dist\BATCRadar\*' -DestinationPath 'dist\BATCRadar-v%VER%.zip' -Force"
if errorlevel 1 (
  echo  ZIP fehlgeschlagen.
  pause
  exit /b 1
)

echo.
echo  Fertig.
echo  Ordner: dist\BATCRadar\
echo  ZIP:    dist\BATCRadar-v%VER%.zip
echo.
echo  Zum Testen: dist\BATCRadar\BATCRadar.exe  (MSFS + BeyondATC laufen lassen)
echo.
pause
