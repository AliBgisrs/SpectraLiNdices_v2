@echo off
setlocal
title PlotSpectra
cd /d "%~dp0"
REM ----------------------------------------------------------------------------
REM  PlotSpectra one-click launcher for Windows.
REM  First run: creates a private Python environment in .venv and installs the
REM  packages - needs internet once. Later runs start in seconds.
REM  Options: PlotSpectra.bat --port 8800 --no-browser
REM ----------------------------------------------------------------------------

set "PY="
py -3 -c "import sys" >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if defined PY goto :havepy
python -c "import sys" >nul 2>nul
if not errorlevel 1 set "PY=python"
if defined PY goto :havepy

echo.
echo  Python 3.10 or newer was not found on this computer.
echo  1. Download it from  https://www.python.org/downloads/
echo  2. In the installer tick  "Add python.exe to PATH"
echo  3. Double-click this file again.
echo.
pause
exit /b 1

:havepy
if exist ".venv\Scripts\python.exe" goto :haveenv
echo  Creating a private Python environment in .venv ...
%PY% -m venv .venv
if errorlevel 1 goto :fail_venv

:haveenv
set "VPY=.venv\Scripts\python.exe"
if exist ".venv\.installed" goto :run
echo  Installing packages - this happens only once and takes a few minutes ...
"%VPY%" -m pip install --upgrade pip >nul 2>nul
"%VPY%" -m pip install -r requirements.txt
if errorlevel 1 goto :fail_pip
echo ok> ".venv\.installed"

:run
echo.
echo  Starting PlotSpectra ... keep this window open while you use the app.
echo  Close this window, or press Ctrl+C, to stop it.
echo.
"%VPY%" app\server.py %*
echo.
echo  PlotSpectra stopped.
pause
exit /b 0

:fail_venv
echo.
echo  Could not create the .venv environment. Try: %PY% -m venv .venv
pause
exit /b 1

:fail_pip
echo.
echo  Package installation failed. Check your internet connection, then run this file again.
pause
exit /b 1
