@echo off
REM ---------------------------------------------------------------------------
REM  MedTracker - start it so the phone on the same Wi-Fi can reach it too.
REM
REM  Same application, same process, same database. The only difference from
REM  start.bat is which network interfaces it listens on: this one answers the
REM  whole local network instead of only this computer.
REM
REM  READ THIS FIRST. There is no login. Anyone on the same Wi-Fi who opens the
REM  address below sees your medications, your appointments and your doctors —
REM  unless you turn on the PIN in Settings -> Security, which is what it is for.
REM  Never do this on a network you do not control, and never forward the port
REM  to the internet.
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Virtual environment not found. Run scripts\install.bat first.
  pause
  exit /b 1
)

set MEDTRACKER_HOST=0.0.0.0
if "%MEDTRACKER_PORT%"=="" set MEDTRACKER_PORT=8000

echo.
echo  On this computer:  http://127.0.0.1:%MEDTRACKER_PORT%
echo.
echo  On your phone, use one of these addresses:
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do (
  for /f "tokens=* delims= " %%b in ("%%a") do echo      http://%%b:%MEDTRACKER_PORT%
)
echo.
echo  If the phone cannot connect, Windows Firewall is blocking the port.
echo  Open PowerShell as administrator, once, and run:
echo.
echo      New-NetFirewallRule -DisplayName "MedTracker" -Direction Inbound ^
echo        -Action Allow -Protocol TCP -LocalPort %MEDTRACKER_PORT% -Profile Private
echo.
echo  Press Ctrl+C to stop.
echo.

start "" http://127.0.0.1:%MEDTRACKER_PORT%
".venv\Scripts\python.exe" -m app.main
