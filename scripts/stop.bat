@echo off
REM ---------------------------------------------------------------------------
REM  MedTracker - stop every background instance of the app.
REM  Use this when it was started by the scheduled task (no console window).
REM  If you started it with start.bat, just press Ctrl+C in that window.
REM ---------------------------------------------------------------------------
setlocal

echo Stopping MedTracker...
for /f "tokens=2 delims=," %%p in ('tasklist /fi "imagename eq pythonw.exe" /fo csv /nh 2^>nul') do (
  taskkill /pid %%~p /f >nul 2>nul
)

REM Also stop anything holding port 8000 (the console-mode instance).
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:"TCP .*:8000 .*LISTENING"') do (
  taskkill /pid %%p /f >nul 2>nul
)

echo Done.
pause
