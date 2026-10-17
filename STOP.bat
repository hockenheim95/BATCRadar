@echo off
cd /d "%~dp0"
echo.
echo  Beende BATCRadar-Server (falls aktiv) ...
echo.
python tools\efb_ctl.py stop
echo.
if /I not "%~1"=="/q" pause
