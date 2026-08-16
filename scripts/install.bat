@echo off
REM ---------------------------------------------------------------------------
REM  MedTracker - one-time setup
REM  Creates a virtual environment in .venv and installs the dependencies.
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0.."

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python was not found in PATH.
  echo Install Python 3.11 or newer from https://www.python.org/downloads/windows/
  echo and make sure "Add python.exe to PATH" is checked.
  pause
  exit /b 1
)

if not exist ".venv" (
  echo Creating virtual environment...
  python -m venv .venv
  if errorlevel 1 goto :failed
)

echo Installing dependencies...
call ".venv\Scripts\python.exe" -m pip install --upgrade pip
call ".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :failed

echo.
echo Done. Start the application with scripts\start.bat
pause
exit /b 0

:failed
echo.
echo [ERROR] Setup failed. Check the messages above.
pause
exit /b 1
