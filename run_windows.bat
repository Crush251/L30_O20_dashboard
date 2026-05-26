@echo off
setlocal

cd /d "%~dp0"

echo ========================================
echo L30/O20 Dashboard Launcher
echo ========================================
echo.

set "DLL_FOUND="

if exist ".\libcanbus\HCanbus.dll" set "DLL_FOUND=1"
if exist ".\HCanbus.dll" set "DLL_FOUND=1"
if exist "..\HCanbus.dll" set "DLL_FOUND=1"

if defined L30_CANBUS_LIB (
    if exist "%L30_CANBUS_LIB%" set "DLL_FOUND=1"
)

if not defined DLL_FOUND (
    echo [ERROR] HCanbus.dll not found.
    echo.
    echo Please place HCanbus.dll in one of these paths:
    echo   L30_06\l30_o20_dashboard\libcanbus\HCanbus.dll
    echo   L30_06\l30_o20_dashboard\HCanbus.dll
    echo   L30_06\HCanbus.dll
    echo.
    echo Or set environment variable L30_CANBUS_LIB to the DLL path.
    echo.
    pause
    exit /b 1
)

where uv >nul 2>nul
if errorlevel 1 (
    echo [ERROR] uv command not found.
    echo.
    echo Please install uv or make sure uv is added to PATH.
    echo.
    pause
    exit /b 1
)

echo [OK] Environment check passed.
echo Starting l30-o20-dashboard...
echo.

uv run l30-o20-dashboard %*

if errorlevel 1 (
    echo.
    echo [ERROR] Program exited with error.
    pause
    exit /b 1
)

echo.
echo Program finished.
pause