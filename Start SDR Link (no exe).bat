@echo off
rem Starts the app like "SDR Link.exe" does, for PCs where the .exe is blocked.
cd /d "%~dp0"
if not exist python_path.txt (
    echo Run Setup.bat first.
    pause
    exit /b 1
)
set /p PY=<python_path.txt
for %%F in ("%PY%") do set "ROOT=%%~dpF"
set "ROOT=%ROOT:~0,-1%"
set "PATH=%ROOT%;%ROOT%\Library\mingw-w64\bin;%ROOT%\Library\usr\bin;%ROOT%\Library\bin;%ROOT%\Scripts;%PATH%"
start "" "%ROOT%\pythonw.exe" "%~dp0app.py"
