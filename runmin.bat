@echo off
REM ---------------------------------------------------------------------------
REM Agentic coding harness - Windows setup & run script
REM Creates a virtual environment, installs dependencies, and runs run.py
REM ---------------------------------------------------------------------------

setlocal enabledelayedexpansion

REM --- Remember where the user invoked this script from. run.py must operate
REM     on THIS folder (the caller's working directory), not the harness
REM     install folder. ---
set "CALLER_DIR=%CD%"
set "SCRIPT_DIR=%~dp0"

REM --- Switch to the harness folder only for environment setup. ---
cd /d "%SCRIPT_DIR%"

REM --- Locate a Python interpreter ---
set "PYTHON_CMD="
where py >nul 2>&1 && set "PYTHON_CMD=py -3"
if not defined PYTHON_CMD (
    where python >nul 2>&1 && set "PYTHON_CMD=python"
)
if not defined PYTHON_CMD (
    echo [ERROR] Python was not found on PATH. Install Python 3.13+ and retry.
    exit /b 1
)

REM --- Create the virtual environment if missing ---
if not exist ".venv\Scripts\python.exe" (
    echo [setup] Creating virtual environment in .venv ...
    %PYTHON_CMD% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create the virtual environment.
        exit /b 1
    )
)

set "VENV_PY=%SCRIPT_DIR%.venv\Scripts\python.exe"

REM --- Upgrade pip (quietly; only warnings/errors are shown) ---
"%VENV_PY%" -m pip install --quiet --upgrade pip
if errorlevel 1 (
    echo [ERROR] Failed to upgrade pip.
    exit /b 1
)

REM --- Install dependencies, capturing output so we can stay quiet when
REM     everything is already satisfied and only report real installs ---
set "PIP_LOG=%TEMP%\pyharness_pip_%RANDOM%.log"
"%VENV_PY%" -m pip install -r requirements.txt > "%PIP_LOG%" 2>&1
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies.
    type "%PIP_LOG%"
    del "%PIP_LOG%" 2>nul
    exit /b 1
)

REM Only announce and show details when packages were actually installed.
findstr /c:"Successfully installed" "%PIP_LOG%" >nul 2>&1
if not errorlevel 1 (
    echo [setup] Installing dependencies from requirements.txt ...
    findstr /v /c:"Requirement already satisfied" "%PIP_LOG%"
)
del "%PIP_LOG%" 2>nul

REM --- Run the program in the context of the caller's folder ---
REM The harness tools resolve paths against HARNESS_WORKSPACE (see
REM tools/_common.py). Default it to the caller's directory and switch the
REM working directory there so the agent acts where runmin was launched from.
if not defined HARNESS_WORKSPACE set "HARNESS_WORKSPACE=%CALLER_DIR%"
if not defined BAREMINIMUM_DIR set "BAREMINIMUM_DIR=%SCRIPT_DIR%"
if "%BAREMINIMUM_DIR:~-1%"=="\" set "BAREMINIMUM_DIR=%BAREMINIMUM_DIR:~0,-1%"

cd /d "%CALLER_DIR%"
echo [setup] Starting run.py in "%CALLER_DIR%" ...
"%VENV_PY%" "%SCRIPT_DIR%run.py" %*
set "EXIT_CODE=%errorlevel%"

endlocal & exit /b %EXIT_CODE%
