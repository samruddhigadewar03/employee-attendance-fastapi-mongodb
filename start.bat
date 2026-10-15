@echo off
echo ========================================
echo   HROne Attendance API — Startup Script
echo ========================================
echo.

:: Check MongoDB service
echo [1/3] Checking MongoDB service...
sc query MongoDB >nul 2>&1
if %errorlevel% == 0 (
    sc start MongoDB >nul 2>&1
    echo       MongoDB service started.
) else (
    echo       MongoDB not installed as service.
    echo       Please ensure mongod is running on localhost:27017
    echo       See README.md for installation instructions.
)

echo.
echo [2/3] Activating virtual environment...
call .venv\Scripts\activate.bat

echo.
echo [3/3] Starting HROne API...
echo       API docs : http://localhost:8000/docs
echo       Dashboard: http://localhost:8000/ui
echo       Health   : http://localhost:8000/health
echo.
uvicorn app.main:app --reload --port 8000
