@echo off
setlocal ENABLEDELAYEDEXPANSION

:: перейти в папку скрипта
cd /d "%~dp0"

:: --- 1) venv приоритетно ---
if exist "%~dp0.venv\Scripts\activate.bat" (
  call "%~dp0.venv\Scripts\activate.bat"
  goto :run_with_active_venv
)
if exist "%~dp0venv\Scripts\activate.bat" (
  call "%~dp0venv\Scripts\activate.bat"
  goto :run_with_active_venv
)

:: --- 2) py-launcher (3.13 -> 3.12 -> 3.11 -> 3) ---
set "PYCMD="
for %%V in (3.13 3.12 3.11 3) do (
  py -%%V -c "import sys; print(sys.version)" >nul 2>&1 && set "PYCMD=py -%%V" && goto :run_with_py
)

:: --- 3) fallback на python из PATH ---
where python >nul 2>&1 && set "PYCMD=python" && goto :run_with_py

echo [ERROR] Python не найден. Установи Python 3.11+ или создай venv в папке проекта.
pause
exit /b 1

:run_with_active_venv
python -u main.py
goto :post

:run_with_py
%PYCMD% -u main.py

:post
:: создать ярлык на рабочем столе (один раз)
set "SHORTCUT=%USERPROFILE%\Desktop\Planner.lnk"
if not exist "%SHORTCUT%" (
  powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ws=New-Object -ComObject WScript.Shell;" ^
    "$t=[Environment]::GetFolderPath('Desktop')+'\Planner.lnk';" ^
    "$s=$ws.CreateShortcut($t);" ^
    "$s.TargetPath='""%~dp0run_silent.vbs""';" ^
    "$s.WorkingDirectory='""%~dp0""';" ^
    "if (Test-Path '""%~dp0assets\icon.ico""') {$s.IconLocation='""%~dp0assets\icon.ico""'};" ^
    "$s.Save()"
)

endlocal
