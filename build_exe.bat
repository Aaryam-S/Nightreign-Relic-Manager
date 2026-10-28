@echo off
rem OPTIONAL: builds a standalone NightreignRelicManager.exe (no Python needed to run it). Needs Python + internet once.
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" ( py -3 -m venv .venv || (echo Python 3 not found & pause & exit /b 1) )
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt pyinstaller || (pause & exit /b 1)
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --windowed --name NightreignRelicManager app.py || (pause & exit /b 1)
xcopy /e /i /y data "dist\NightreignRelicManager\data" >nul
echo.
echo Done: dist\NightreignRelicManager\NightreignRelicManager.exe  ^(keep the data folder next to it; backups/ is created there^)
pause
