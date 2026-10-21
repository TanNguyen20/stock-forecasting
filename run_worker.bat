@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Chua co moi truong ao. Chay setup.bat truoc.
  pause
  exit /b 1
)
.venv\Scripts\python.exe worker.py %*
