@echo off
REM LastMile Alert - Windows startup script.
REM Installs dependencies if needed, then launches the app.
cd /d "%~dp0"
echo === LastMile Alert ===
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] pip failed. Make sure Python 3.11+ is installed and "Add Python to PATH" was ticked.
    pause
    exit /b 1
)
python app.py
pause
