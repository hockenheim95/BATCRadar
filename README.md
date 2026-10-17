# BATCRadar

Moving map / EFB for Microsoft Flight Simulator with live traffic from BeyondATC.

## Dein Alltag (Anfänger)

Du arbeitest **immer** im Ordner `BATCRadar` (nicht in `efb-moving-map`, nicht in `archive`).

1. **Ändern und testen**  
   MSFS + BeyondATC starten, dann `START.bat`. Im Konsolenfenster steht die Tablet-Adresse. So entwickelst du. Dafür brauchst *du* Python — Endnutzer später nicht.

2. **Version hochzählen**  
   In der Datei `VERSION` steht z. B. `0.16.417`. Vor einem Release die letzte Zahl erhöhen (`0.16.418`). Die Karte zeigt `v…` unten.

3. **Speichern (Git statt Archiv-Ordner)**  
   Früher lag jede Version als Kopie in `archive/`. Das macht Git jetzt:
   - Cursor: Änderungen committen, kurz beschreiben, **Push**.
   - Damit liegt der Stand auf GitHub: https://github.com/hockenheim95/BATCRadar  
   Alte Stände holst du über die Commit-Historie, nicht über Ordnerkopien.

4. **Build für andere Leute**  
   `BUILD.bat` doppelklicken (wieder Python nötig, nur bei dir). Es entsteht:
   - `dist\BATCRadar\` — zum selbst Testen (`BATCRadar.exe`)
   - `dist\BATCRadar-v….zip` — das lädst du auf Flightsim.to hoch  

   Im ZIP starten Nutzer nur `BATCRadar.exe`. Kein Python.

5. **Veröffentlichen**  
   ZIP lokal mit MSFS testen. Dann Flightsim.to: neues Utility, ZIP anhängen, GitHub-Link, kurze Anleitung (MSFS + BeyondATC, Firewall erlauben, Adresse aus dem Fenster). Windows Defender kann „Trotzdem ausführen“ verlangen — das ist bei solchen EXEs normal.

Nicht ins ZIP / nicht committen: `data\procs`, Tile-Caches, `archive`, `.venv`.

## Run (Entwicklung)

1. [Python 3](https://www.python.org/downloads/) (Add to PATH).
2. `START.bat`
3. URL aus der Konsole öffnen.

Traffic: BeyondATC (Port 41717). Ownship-Map-Position/Spur: SimConnect; Heading/Höhe/GS und Meta: BATC. SimConnect auch für Prozeduren und als Ownship-Fallback ohne BATC-Player.
