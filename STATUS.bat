@echo off
cd /d "%~dp0"
echo.
echo  BATCRadar — Server-Status
echo  -------------------------
echo.
python tools\efb_ctl.py status
echo.
pause
