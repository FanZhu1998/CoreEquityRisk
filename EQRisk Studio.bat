@echo off
setlocal
title EQRisk Studio
rem Double-click: starts EQRisk Studio (if it is not already running) and opens it in its own window.
rem The server runs in a minimized window named "EQRisk Studio server"; close that window, or use
rem System > Jobs & settings > Shut down, to stop it.

cd /d "%~dp0"
set "PORT=8520"
set "URL=http://127.0.0.1:%PORT%/"
set "UV=uv"
where uv >nul 2>nul || set "UV=%USERPROFILE%\.local\bin\uv.exe"

powershell -NoProfile -Command "try { (New-Object Net.Sockets.TcpClient('127.0.0.1', %PORT%)).Close(); exit 0 } catch { exit 1 }"
if errorlevel 1 (
  echo Starting EQRisk Studio ...
  start "EQRisk Studio server" /min "%UV%" run --no-sync streamlit run app\studio.py --server.port %PORT% --server.address 127.0.0.1
)

echo Waiting for the Studio to come up ...
powershell -NoProfile -Command "for ($i = 0; $i -lt 120; $i++) { try { (New-Object Net.Sockets.TcpClient('127.0.0.1', %PORT%)).Close(); exit 0 } catch { Start-Sleep -Milliseconds 500 } }; exit 1"
if errorlevel 1 (
  echo The Studio did not start. Check the "EQRisk Studio server" window for the error.
  pause
  exit /b 1
)

set "EDGE=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if not exist "%EDGE%" set "EDGE=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
if exist "%EDGE%" (start "" "%EDGE%" --app=%URL% --window-size=1480,940) else (start "" %URL%)
exit /b 0
