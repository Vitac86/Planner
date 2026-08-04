@echo off
setlocal EnableExtensions

rem ---------------------------------------------------------------------------
rem Planner (PySide6 + QML) launcher.
rem Double-click this file in Explorer to start the application.
rem Interpreter order: .venv\Scripts\python.exe -> py -3 -> python
rem ---------------------------------------------------------------------------

rem UTF-8 console + interpreter I/O so Cyrillic output renders correctly.
chcp 65001 >nul 2>&1
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

rem Always operate relative to this script, never a hard-coded path.
cd /d "%~dp0" || (
  echo [ERROR] Cannot change directory to "%~dp0".
  pause
  exit /b 1
)

set "APP=%~dp0run_desktop.py"
if not exist "%APP%" (
  echo [ERROR] run_desktop.py not found next to this launcher:
  echo         "%APP%"
  pause
  exit /b 1
)

rem --- 1) project virtual environment (preferred) ----------------------------
set "PY="
if exist "%~dp0.venv\Scripts\python.exe" set "PY="%~dp0.venv\Scripts\python.exe""
if not defined PY if exist "%~dp0venv\Scripts\python.exe" set "PY="%~dp0venv\Scripts\python.exe""

rem --- 2) py launcher --------------------------------------------------------
if not defined PY (
  py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
)

rem --- 3) python on PATH -----------------------------------------------------
if not defined PY (
  python -c "import sys" >nul 2>&1 && set "PY=python"
)

if not defined PY (
  echo [ERROR] Python 3 not found.
  echo         Install Python 3.11+ from https://www.python.org/downloads/
  echo         or create a virtual environment in this folder:
  echo             python -m venv .venv
  echo             .venv\Scripts\python.exe -m pip install -r requirements.txt
  pause
  exit /b 1
)

rem --- dependency check (fast import probe, never reinstalls) ----------------
%PY% -c "import PySide6" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] PySide6 is not installed for the selected interpreter:
  %PY% -c "import sys; print('        ' + sys.executable)" 2>nul
  echo.
  echo         Install the dependencies with:
  echo             python -m pip install -r requirements.txt
  echo.
  pause
  exit /b 1
)

rem --- launch (attached, so startup errors stay visible) ---------------------
%PY% -u "%APP%" %*
set "RC=%ERRORLEVEL%"

rem Pause only on failure; a clean exit closes the window silently.
if not "%RC%"=="0" (
  echo.
  echo [ERROR] Planner exited with code %RC%.
  pause
)

endlocal & exit /b %RC%
