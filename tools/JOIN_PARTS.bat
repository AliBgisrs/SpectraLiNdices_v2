@echo off
setlocal
cd /d "%~dp0"
REM Rebuilds PlotSpectra_sampledata_*.zip from its .partNN pieces and extracts it.
REM Uses Python if available (verifies checksums); otherwise falls back to copy /b.
py -3 -c "import sys" >nul 2>nul
if not errorlevel 1 ( py -3 join_parts.py & goto :end )
python -c "import sys" >nul 2>nul
if not errorlevel 1 ( python join_parts.py & goto :end )
echo Python not found - joining with copy /b, no checksum check.
for %%F in (*.part01) do set "BASE=%%~nF"
if not defined BASE ( echo No .part01 file found. & goto :end )
copy /b "%BASE%.part*" "%BASE%" >nul
echo Joined %BASE% - unzip it with right-click, "Extract All...".
:end
pause
