@echo off
REM Pull the latest version from GitHub and refresh the packages.
setlocal
cd /d "%~dp0"
git pull || (echo Could not pull. Is Git installed and is this folder a clone of the repo? & pause & exit /b 1)
if exist ".venv\Scripts\python.exe" ".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
echo Updated. Start the app with Start-OmniFlowControl.bat
pause
