@echo off
setlocal
cd /d "%~dp0"
title Nightreign Relic Manager

if /i "%~1"=="shortcut" goto :shortcut

rem ---- find Python (py launcher, then PATH, then the usual install folder) ----
set "PYEXE="
set "PYARG="
where py >nul 2>nul && py -3 -c "import sys" >nul 2>nul && (set "PYEXE=py" & set "PYARG=-3")
if not defined PYEXE where python >nul 2>nul && python -c "import sys" >nul 2>nul && set "PYEXE=python"
if not defined PYEXE for /d %%D in ("%LocalAppData%\Programs\Python\Python3*") do if exist "%%~D\python.exe" set "PYEXE=%%~D\python.exe"
if not defined PYEXE (
  echo Python 3 was not found.
  echo Install it from https://www.python.org/downloads/  ^(tick "Add python.exe to PATH"^), then run this again.
  pause
  exit /b 1
)

"%PYEXE%" %PYARG% -c "import tkinter" >nul 2>nul
if errorlevel 1 (
  echo This Python has no Tk support. Re-run the Python installer, choose Modify, and tick "tcl/tk and IDLE".
  pause
  exit /b 1
)

rem ---- first run: private environment + dependencies (only once) ----
if not exist ".venv\Scripts\python.exe" (
  echo First run: setting up, this takes a minute...
  "%PYEXE%" %PYARG% -m venv .venv
  if errorlevel 1 ( echo Could not create the environment. & pause & exit /b 1 )
)
if not exist ".venv\deps_ok.txt" (
  ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
  if errorlevel 1 ( echo Could not install the requirements ^(is the internet on?^). & pause & exit /b 1 )
  echo ok> ".venv\deps_ok.txt"
)

rem ---- launch without a console window; errors go to crash.log ----
start "" ".venv\Scripts\pythonw.exe" app.py %*
exit /b 0

:shortcut
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Nightreign Relic Manager.lnk'); $s.TargetPath='%~f0'; $s.WorkingDirectory='%~dp0'; $s.WindowStyle=7; $s.Save()"
echo Desktop shortcut created.
pause
