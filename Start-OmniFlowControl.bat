@echo off
REM One-click start for the Omni Flow Control Centre on Windows.
REM First run: creates a private Python environment and installs the packages.
REM Every run: starts the dashboard and opens it in your browser.
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel% neq 0 (
  echo Python is not installed. Install Python 3.11 or newer from https://www.python.org/downloads/windows/
  echo and tick "Add python.exe to PATH" in the installer, then run this file again.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating the Python environment, this happens only once...
  py -3 -m venv .venv || (echo Could not create the environment. & pause & exit /b 1)
  ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
  ".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt || (echo Package install failed. & pause & exit /b 1)
)

echo Starting the Omni Flow Control Centre at http://localhost:8501 ...
".venv\Scripts\python.exe" -m streamlit run omniflow_control\app.py --server.address 127.0.0.1 --server.headless true --browser.gatherUsageStats false
pause
