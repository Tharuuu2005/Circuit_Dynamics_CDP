@echo off
setlocal EnableDelayedExpansion
title SDR Link setup
cd /d "%~dp0"
echo.
echo  SDR Link setup: run this once on each PC.
echo.
rem Which Python? 1) the conda environment this window has active (Anaconda Prompt,
rem    "conda activate myenv"), if it has GNU Radio; 2) radioconda; 3) any conda
rem    environment under anaconda3 / miniconda3 / miniforge3 that has GNU Radio.
set "ROOT="
if defined CONDA_PREFIX if exist "%CONDA_PREFIX%\python.exe" (
    "%CONDA_PREFIX%\python.exe" -c "import gnuradio.gr" >nul 2>&1 && set "ROOT=%CONDA_PREFIX%"
)
if not defined ROOT for %%D in ("%USERPROFILE%\radioconda" "%LOCALAPPDATA%\radioconda" "%ProgramData%\radioconda" "%ProgramFiles%\radioconda" "C:\radioconda") do (
    if not defined ROOT if exist "%%~D\python.exe" set "ROOT=%%~D"
)
if not defined ROOT for %%B in ("%USERPROFILE%" "%LOCALAPPDATA%" "%ProgramData%" "C:") do for %%C in (anaconda3 miniconda3 miniforge3 mambaforge) do (
    if not defined ROOT if exist "%%~B\%%C\envs" for /d %%E in ("%%~B\%%C\envs\*") do (
        if not defined ROOT if exist "%%~E\python.exe" (
            "%%~E\python.exe" -c "import gnuradio.gr" >nul 2>&1 && set "ROOT=%%~E"
        )
    )
    if not defined ROOT if exist "%%~B\%%C\python.exe" (
        "%%~B\%%C\python.exe" -c "import gnuradio.gr" >nul 2>&1 && set "ROOT=%%~B\%%C"
    )
)
if not defined ROOT (
    echo  No Python with GNU Radio was found.
    echo  Install radioconda ^(github.com/ryanvolz/radioconda^), or open Anaconda Prompt,
    echo  "conda activate" your GNU Radio environment and run Setup.bat from that window.
    pause
    exit /b 1
)
echo  Using the Python in %ROOT%
set "PATH=%ROOT%;%ROOT%\Library\mingw-w64\bin;%ROOT%\Library\usr\bin;%ROOT%\Library\bin;%ROOT%\Scripts;%PATH%"
"%ROOT%\python.exe" "%~dp0tools\setup_check.py"
set "RESULT=%ERRORLEVEL%"
> "%~dp0python_path.txt" echo %ROOT%\python.exe
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\SDR Link.lnk');" ^
  "$s.TargetPath='%~dp0SDR Link.exe';$s.WorkingDirectory='%~dp0';$s.IconLocation='%~dp0SDR Link.exe,0';$s.Save()" >nul 2>&1
if exist "%USERPROFILE%\Desktop\SDR Link.lnk" echo  A shortcut "SDR Link" is on the desktop.
echo.
pause
exit /b %RESULT%
