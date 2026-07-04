@echo off
title LAC
echo Starting LAC...

set PYTHON=C:\Users\User\AppData\Local\Python\bin\python.exe
set SCRIPT_DIR=%~dp0

if not exist "%PYTHON%" (
    where python >nul 2>&1
    if errorlevel 1 (
        echo Python not found. Please install Python 3.10+ from https://python.org
        pause
        exit /b 1
    )
    set PYTHON=python
)

echo Installing dependencies...
"%PYTHON%" -m pip install flask -q

echo Starting server...
"%PYTHON%" "%SCRIPT_DIR%server.py"

pause
