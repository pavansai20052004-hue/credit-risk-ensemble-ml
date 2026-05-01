$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

Write-Host ""
Write-Host "CrediSense AI local starter" -ForegroundColor Cyan
Write-Host "This starts BOTH frontend and backend through Flask." -ForegroundColor Cyan
Write-Host ""

if (-not (Test-Path ".\venv\Scripts\python.exe")) {
    Write-Host "venv was not found. Creating it now..." -ForegroundColor Yellow
    if (Get-Command py -ErrorAction SilentlyContinue) {
        py -3 -m venv venv
    }
    elseif (Get-Command python -ErrorAction SilentlyContinue) {
        python -m venv venv
    }
    else {
        throw "Python was not found. Install Python, then run this script again."
    }
}

$python = ".\venv\Scripts\python.exe"

Write-Host "Installing/updating dependencies..." -ForegroundColor Yellow
& $python -m pip install -r requirements.txt

Write-Host "Preparing database..." -ForegroundColor Yellow
& $python init_db.py

Write-Host ""
Write-Host "Starting CrediSense AI..." -ForegroundColor Green
Write-Host "Open: http://127.0.0.1:5000" -ForegroundColor Green
Write-Host "Do not use gunicorn on Windows. Gunicorn is only for Render/Linux." -ForegroundColor DarkYellow
Write-Host ""

& $python app.py
