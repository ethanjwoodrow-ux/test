@echo off
title UC Learn — Login
echo ============================================
echo  UC Learn — Save Login Session
echo ============================================
echo.
echo A browser window will open. Log in to UC Learn
echo as you normally would (including SSO if needed).
echo The window will close automatically when done.
echo.
echo DO NOT close this window until the browser closes.
echo.
pause
python scripts/login.py
if errorlevel 1 (
    echo.
    echo [ERROR] Login failed. Check your credentials in .env
    pause
    exit /b 1
)
echo.
echo [OK] Session saved. You won't need to log in again
echo      for several weeks.
echo.
pause
