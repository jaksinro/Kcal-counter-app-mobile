@echo off
rem Arrete le serveur CalTrack (port 8001) puis le relance sans fenetre.
cd /d "%~dp0"
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }"
timeout /t 1 /nobreak >nul
start "" pythonw server.py 8001
echo CalTrack redemarre : http://localhost:8001
timeout /t 3 >nul
