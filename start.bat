@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1

set "PY=python"
where py >nul 2>nul
if not errorlevel 1 set "PY=py -3"

if not exist ".venv\Scripts\python.exe" (
  echo [1/4] Creating virtual environment...
  %PY% -m venv .venv
  if errorlevel 1 goto error
)

echo [2/4] Installing backend dependencies...
".venv\Scripts\python.exe" -m pip install -r backend\requirements.txt
if errorlevel 1 goto error

echo [3/4] Starting AI digital human backend...
echo [4/4] Open http://127.0.0.1:8000 in your browser.
".venv\Scripts\python.exe" -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
goto end

:error
echo Startup failed. Check Python installation and network access for pip.
pause

:end
endlocal
