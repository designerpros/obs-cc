@echo off
REM Startup script for OBS News Ticker Backend (Windows)

echo ===================================
echo OBS News Ticker Backend
echo ===================================
echo.

REM Check if config.json exists
if not exist config.json (
    echo [ERROR] config.json not found!
    echo.
    echo Please create config.json from config.example.json:
    echo   copy config.example.json config.json
    echo.
    echo Then edit config.json and add your API keys:
    echo   - assemblyai_api_key
    echo   - anthropic_api_key
    echo.
    pause
    exit /b 1
)

REM Check if virtual environment exists
if not exist venv (
    echo [SETUP] Creating virtual environment...
    python -m venv venv
)

REM Activate virtual environment
echo [SETUP] Activating virtual environment...
call venv\Scripts\activate.bat

REM Install dependencies
echo [SETUP] Installing dependencies...
pip install -q -r requirements.txt

echo.
echo [OK] Starting backend service...
echo    URL: http://localhost:8765
echo.
echo Press Ctrl+C to stop
echo.

REM Run the backend
python -m backend.main
