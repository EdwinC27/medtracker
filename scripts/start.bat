@echo off
REM ---------------------------------------------------------------------------
REM  MedTracker - start the application
REM  Runs the web server AND the background notification scheduler in one
REM  process. Keep this window open (or minimise it) for reminders to fire.
REM  Stop it with Ctrl+C or by closing the window.
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Virtual environment not found. Run scripts\install.bat first.
  pause
  exit /b 1
)

echo Starting MedTracker on http://127.0.0.1:8000
echo Press Ctrl+C to stop.
start "" http://127.0.0.1:8000
".venv\Scripts\python.exe" -m app.main
