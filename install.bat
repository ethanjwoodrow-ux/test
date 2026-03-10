@echo off
title UC Learn Automation — Installer
echo ============================================
echo  UC Learn Automation — One-Click Installer
echo ============================================
echo.

:: Check Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found.
    echo Please install Python 3.11+ from https://python.python.org/downloads/
    echo Make sure to tick "Add Python to PATH" during install.
    pause
    exit /b 1
)

echo [1/3] Installing Python packages...
pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] pip install failed. Check your internet connection.
    pause
    exit /b 1
)

echo.
echo [2/3] Installing Playwright browser (Chromium)...
playwright install chromium
if errorlevel 1 (
    echo [ERROR] Playwright install failed.
    pause
    exit /b 1
)

echo.
echo [3/3] Setting up credentials...
if not exist .env (
    call setup_credentials.bat
) else (
    echo      .env already exists — skipping credential setup.
    echo      Run setup_credentials.bat to change them.
)

echo.
echo ============================================
echo  Install complete!
echo  Next step: double-click LOGIN.bat
echo ============================================
pause
