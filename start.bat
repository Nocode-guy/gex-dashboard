@echo off
echo ========================================
echo   GEX Dashboard Startup
echo ========================================
echo.

cd /d "%~dp0"

REM Check if Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Please install Python 3.8+
    pause
    exit /b 1
)

REM Check if dependencies are installed
echo Checking dependencies...
pip show fastapi >nul 2>&1
if errorlevel 1 (
    echo Installing dependencies...
    pip install -r backend\requirements.txt
)

REM Check for API key
if defined TRADIER_API_KEY (
    echo Using Tradier API key: %TRADIER_API_KEY:~0,8%...
) else (
    echo No TRADIER_API_KEY found - using mock data
    echo To use real data, set: set TRADIER_API_KEY=your_key
)

echo.
echo Starting GEX Dashboard API on http://localhost:5000
echo Press Ctrl+C to stop
echo.

cd backend
python app.py
