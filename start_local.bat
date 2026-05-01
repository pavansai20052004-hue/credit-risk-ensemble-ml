@echo off
setlocal

cd /d "%~dp0"

echo.
echo CrediSense AI local starter
echo This starts BOTH frontend and backend through Flask.
echo.

if not exist "venv\Scripts\python.exe" (
    echo venv was not found. Creating it now...
    py -3 -m venv venv
    if errorlevel 1 (
        python -m venv venv
    )
)

if not exist "venv\Scripts\python.exe" (
    echo Python virtual environment could not be created.
    echo Install Python, then run this file again.
    pause
    exit /b 1
)

echo Installing/updating dependencies...
"venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo Dependency install failed.
    pause
    exit /b 1
)

echo Preparing database...
"venv\Scripts\python.exe" init_db.py
if errorlevel 1 (
    echo Database setup failed.
    pause
    exit /b 1
)

echo.
echo Starting CrediSense AI...
echo Open: http://127.0.0.1:5000
echo Do not use gunicorn on Windows. Gunicorn is only for Render/Linux.
echo.

"venv\Scripts\python.exe" app.py
pause
