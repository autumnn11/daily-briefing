@echo off
cd /d "%~dp0.."
if not exist "briefings" mkdir "briefings"
call .venv\Scripts\activate.bat 2>nul
python -m src.run_daily >> "briefings\run.log" 2>&1
exit /b %errorlevel%
