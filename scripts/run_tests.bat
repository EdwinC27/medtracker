@echo off
REM Runs the test-suite inside the project's virtual environment.
setlocal
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Virtual environment not found. Run scripts\install.bat first.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" -m pytest -v
pause
