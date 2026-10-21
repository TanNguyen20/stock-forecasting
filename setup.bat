@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe py -3.13 -m venv .venv || python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements-ml.txt
if not exist .env copy .env.example .env
echo.
echo Xong. Chay run_app.bat de mo ung dung, run_worker.bat de bat canh bao nen.
pause
