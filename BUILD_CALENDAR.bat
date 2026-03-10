@echo off
title UC Learn — Build Deadline Calendar
echo Building deadline calendar...
python scripts/build_calendar.py
if errorlevel 0 (
    echo.
    echo Opening Downloads folder...
    explorer "%USERPROFILE%\Desktop\Uni\2026"
)
pause
