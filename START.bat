@echo off
cd /d "%~dp0"
echo.
echo  BATCRadar
echo  -------------------
echo  Fenster offen lassen. Die Tablet-Adresse steht danach im Fenster.
echo.
echo  MSFS + BeyondATC laufen lassen.
echo  Traffic kommt aus BATC (auch ausserhalb der Sichtweite).
echo.
python -c "import flask,waitress" 2>nul
if errorlevel 1 (
  echo  Installiere Flask und Waitress ...
  python -m pip install --disable-pip-version-check flask waitress
  if errorlevel 1 (
    echo  Installation fehlgeschlagen.
    pause
    exit /b 1
  )
)
python server.py
echo.
pause
