@echo off
REM Build "Medication Organizer.exe".
REM
REM   scripts\build_windows.bat
REM
REM Produces dist\Medication Organizer\Medication Organizer.exe together with
REM everything it needs. Copy that whole folder wherever you want the
REM application to live.
REM
REM The folder holds the program and NOTHING of yours. The packaged application
REM uses the SAME data folder as scripts\start.bat, so both ways of starting it
REM show the same medications; it finds the installation next to it on its first
REM run and remembers where it went in %LOCALAPPDATA%\MedTracker. Rebuilding or
REM replacing this folder can never touch your database.

setlocal
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
  echo The virtual environment is missing. Run scripts\install.bat first.
  exit /b 1
)

echo == Installing the build and desktop dependencies ==
call .venv\Scripts\python.exe -m pip install --upgrade pip
call .venv\Scripts\python.exe -m pip install -r requirements.txt
call .venv\Scripts\python.exe -m pip install pyinstaller
if errorlevel 1 (
  echo Could not install the build dependencies.
  exit /b 1
)

echo.
echo == Running the tests before building ==
call .venv\Scripts\python.exe -m pytest -q
if errorlevel 1 (
  echo The tests failed. The build was not started.
  exit /b 1
)

echo.
echo == Building ==
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
call .venv\Scripts\python.exe -m PyInstaller medtracker.spec --noconfirm
if errorlevel 1 (
  echo The build failed.
  exit /b 1
)

echo.
echo Done.
echo   dist\Medication Organizer\Medication Organizer.exe
echo.
echo Copy the whole "Medication Organizer" folder to where you want it to live.
echo.
echo Run it once from dist\ before moving it anywhere: on that first run it finds
echo your existing data folder and writes down where it is, so the .exe and
echo scripts\start.bat share one database. Rebuilding never touches your data.
endlocal
