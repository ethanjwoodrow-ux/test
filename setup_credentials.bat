@echo off
title UC Learn — Credential Setup
echo ============================================
echo  UC Learn Credential Setup
echo ============================================
echo.
echo Your credentials are saved ONLY to .env on
echo this computer. They are never uploaded anywhere.
echo.

set /p UC_USER=Enter your UC username (e.g. abc123):
set /p UC_PASS=Enter your UC password:

:: Write .env file
(
echo UC_USERNAME=%UC_USER%
echo UC_PASSWORD=%UC_PASS%
echo.
echo # Where to save downloaded files
echo UC_BASE_DIR=%USERPROFILE%\Desktop\Uni\2026
echo.
echo # Days ahead to show in digest
echo UC_DIGEST_DAYS=7
echo.
echo # Set to false to see the browser window
echo UC_HEADLESS=true
) > .env

echo.
echo [OK] Credentials saved to .env
echo      To change them, run this script again or edit .env directly.
echo.
pause
