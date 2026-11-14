@echo off
rem Starts the whole voice agent, each part in its OWN terminal window:
rem   backend (:8100), ngrok tunnel, dashboard (:3100). Close a window to stop that part.
rem   start-all.bat        dashboard in fast production mode (builds first, ~1 min)
rem   start-all.bat dev    dashboard in development mode (hot reload)
rem A part that is already running is left alone, so running this twice opens no broken duplicates.
setlocal
cd /d "%~dp0"
set "ROOT=%~dp0"

set "FRONT_ARGS="
if /i "%~1"=="dev" set "FRONT_ARGS=-Dev"

rem Windows 11 hands "start powershell" to Windows Terminal, which can merge the windows
rem into tabs of one window. "wt -w new" asks it for a separate window every time.
set "USE_WT="
where wt.exe >nul 2>nul && set "USE_WT=1"

call :port_free 8100
if errorlevel 1 (echo Backend :8100 is already running - left as it is.) else (call :open "Voice Agent - Backend :8100" start-backend.ps1)

call :tunnel_free
if errorlevel 1 (echo ngrok tunnel is already running - left as it is.) else (call :open "Voice Agent - Tunnel (ngrok)" start-tunnel.ps1)

call :port_free 3100
if errorlevel 1 (echo Dashboard :3100 is already running - left as it is.) else (call :open "Voice Agent - Dashboard :3100" start-frontend.ps1 %FRONT_ARGS%)

rem Open the dashboard in the browser as soon as it answers on port 3100.
rem conhost + Hidden keeps this helper invisible (Windows Terminal would show it as a window).
start "" /min conhost.exe powershell -NoProfile -WindowStyle Hidden -Command "for ($i = 0; $i -lt 150; $i++) { try { Invoke-WebRequest http://localhost:3100 -UseBasicParsing -TimeoutSec 3 | Out-Null; Start-Process http://localhost:3100; break } catch { Start-Sleep 2 } }"

echo.
echo Backend, tunnel and dashboard each run in their own window.
echo The dashboard opens in your browser at http://localhost:3100 when it is ready.
timeout /t 5 >nul
exit /b 0


rem ---- helpers ----

rem Opens one part in a new window. Args: "window title", script file, optional script args.
:open
if defined USE_WT (
    start "" wt.exe -w new --title %1 --suppressApplicationTitle powershell -NoExit -NoProfile -ExecutionPolicy Bypass -File "%ROOT%%~2" %3
) else (
    start %1 powershell -NoExit -NoProfile -ExecutionPolicy Bypass -File "%ROOT%%~2" %3
)
exit /b 0

rem Sets errorlevel 0 when nothing answers on the port, 1 when something is already running there.
:port_free
powershell -NoProfile -Command "try { (New-Object Net.Sockets.TcpClient('127.0.0.1', %1)).Close(); exit 1 } catch { exit 0 }"
exit /b %errorlevel%

rem Sets errorlevel 0 when no ngrok tunnel to :8100 is running, 1 when one already is.
:tunnel_free
powershell -NoProfile -Command "if (Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'ngrok.exe' -and $_.CommandLine -match 'http 8100' }) { exit 1 } else { exit 0 }"
exit /b %errorlevel%
