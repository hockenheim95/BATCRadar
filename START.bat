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
python -c "import flask,waitress,SimConnect" 2>nul
if errorlevel 1 (
  echo  Installiere Python-Pakete ...
  python -m pip install --disable-pip-version-check -r requirements.txt
  if errorlevel 1 (
    echo  Installation fehlgeschlagen.
    pause
    exit /b 1
  )
)
python server.py
echo.
pause
