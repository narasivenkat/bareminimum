@echo off
REM ---------------------------------------------------------------------------
REM setup.bat - Add this folder to the user PATH so runmin.bat can be run from
REM anywhere by simply typing:  runmin
REM
REM Uses the .NET environment API (via PowerShell) instead of setx because it
REM preserves existing PATH entries, is not truncated at 1024 characters, and
REM only appends when the folder is not already present.
REM ---------------------------------------------------------------------------

setlocal

REM Folder that contains this script (and runmin.bat), without trailing backslash.
set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

if not exist "%SCRIPT_DIR%\runmin.bat" (
    echo [ERROR] runmin.bat was not found in "%SCRIPT_DIR%".
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
 "$dir='%SCRIPT_DIR%'; $cur=[Environment]::GetEnvironmentVariable('Path','User'); $parts=@(); if($cur){$parts=$cur -split ';' | Where-Object {$_ -ne ''}}; if($parts -icontains $dir){Write-Host '[setup] Already on PATH:' $dir; exit 0}; [Environment]::SetEnvironmentVariable('Path', (($parts + $dir) -join ';'), 'User'); Write-Host '[setup] Added to PATH:' $dir"

if errorlevel 1 (
    echo [ERROR] Failed to update the user PATH.
    exit /b 1
)

echo [setup] Done. Open a NEW terminal, then run:  runmin
endlocal