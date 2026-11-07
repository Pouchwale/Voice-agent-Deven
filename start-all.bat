@echo off
rem Starts the whole voice agent: backend (:8100), ngrok tunnel, dashboard (:3100).
rem Each part opens in its own window - close a window to stop that part.
rem   start-all.bat        dashboard in fast production mode (builds first, ~1 min)
rem   start-all.bat dev    dashboard in development mode (hot reload)
cd /d "%~dp0"

set "FRONT_ARGS="
if /i "%~1"=="dev" set "FRONT_ARGS=-Dev"

start "Voice Agent - Backend :8100" powershell -NoExit -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-backend.ps1"
start "Voice Agent - Tunnel (ngrok)" powershell -NoExit -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-tunnel.ps1"
start "Voice Agent - Dashboard :3100" powershell -NoExit -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-frontend.ps1" %FRONT_ARGS%

rem Open the dashboard in the browser as soon as it answers on port 3100.
start "" /min powershell -NoProfile -WindowStyle Hidden -Command "for ($i = 0; $i -lt 150; $i++) { try { Invoke-WebRequest http://localhost:3100 -UseBasicParsing -TimeoutSec 3 | Out-Null; Start-Process http://localhost:3100; break } catch { Start-Sleep 2 } }"

echo Started backend, tunnel and dashboard in separate windows.
echo The dashboard will open in your browser at http://localhost:3100 when it is ready.
timeout /t 5 >nul
