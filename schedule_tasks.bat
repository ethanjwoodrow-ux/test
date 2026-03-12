@echo off
title UC Learn — Schedule Automatic Tasks
echo ============================================
echo  UC Learn — Windows Task Scheduler Setup
echo  Run this once to automate everything.
echo  (Right-click > Run as administrator)
echo ============================================
echo.

:: Get the directory this script lives in
set "DIR=%~dp0"
:: Strip trailing backslash
if "%DIR:~-1%"=="\" set "DIR=%DIR:~0,-1%"

:: Find Python
for /f "delims=" %%i in ('where python 2^>nul') do (
    set "PYTHON=%%i"
    goto :found_python
)
echo [ERROR] Python not found. Run install.bat first.
pause
exit /b 1
:found_python
echo [OK] Python: %PYTHON%
echo [OK] Project: %DIR%
echo.

:: Create logs folder
if not exist "%DIR%\logs" mkdir "%DIR%\logs"

:: Helper: create a task that runs a python script
:: Usage: call :make_task <task_name> <script> <schedule_flags>

echo [1/5] Daily digest at 07:30...
schtasks /create /f /tn "UC Learn - Daily Digest" ^
  /tr "\"%PYTHON%\" \"%DIR%\scripts\daily_digest.py\" >> \"%DIR%\logs\digest.log\" 2>&1" ^
  /sc daily /st 07:30
if errorlevel 1 echo      [WARN] Failed to create task — try running as Administrator.

echo [2/5] Check new content at 09:00...
schtasks /create /f /tn "UC Learn - New Content Morning" ^
  /tr "\"%PYTHON%\" \"%DIR%\scripts\check_new_content.py\" >> \"%DIR%\logs\new_content.log\" 2>&1" ^
  /sc daily /st 09:00
if errorlevel 1 echo      [WARN] Failed to create task.

echo [3/5] Check new content at 15:00...
schtasks /create /f /tn "UC Learn - New Content Afternoon" ^
  /tr "\"%PYTHON%\" \"%DIR%\scripts\check_new_content.py\" >> \"%DIR%\logs\new_content.log\" 2>&1" ^
  /sc daily /st 15:00
if errorlevel 1 echo      [WARN] Failed to create task.

echo [4/5] Update grades Mon-Fri at 18:00...
schtasks /create /f /tn "UC Learn - Check Grades" ^
  /tr "\"%PYTHON%\" \"%DIR%\scripts\check_grades.py\" >> \"%DIR%\logs\grades.log\" 2>&1" ^
  /sc weekly /d MON,TUE,WED,THU,FRI /st 18:00
if errorlevel 1 echo      [WARN] Failed to create task.

echo [5/5] Rebuild calendar every Sunday at 08:00...
schtasks /create /f /tn "UC Learn - Build Calendar" ^
  /tr "\"%PYTHON%\" \"%DIR%\scripts\build_calendar.py\" >> \"%DIR%\logs\calendar.log\" 2>&1" ^
  /sc weekly /d SUN /st 08:00
if errorlevel 1 echo      [WARN] Failed to create task.

echo.
echo ============================================
echo  Done! Tasks registered in Task Scheduler.
echo  To view/edit: open taskschd.msc
echo  Logs saved to: %DIR%\logs\
echo ============================================
pause
