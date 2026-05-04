@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1

if not exist ".venv\Scripts\python.exe" (
  echo Virtual environment not found. Run start.bat first.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" backend\smoke_test.py
pause
endlocal
