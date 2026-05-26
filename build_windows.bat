@echo off
setlocal

cd /d "%~dp0"

echo ========================================
echo L30/O20 Dashboard Windows Build
echo ========================================
echo.

if not exist ".\libcanbus\HCanbus.dll" (
    echo [ERROR] 缺少 libcanbus\HCanbus.dll。
    echo.
    echo 请把 Windows 的 HCanbus.dll 放到：
    echo   l30_o20_dashboard\libcanbus\HCanbus.dll
    echo.
    echo 然后再重新打包。
    echo.
    pause
    exit /b 1
)

where uv >nul 2>nul
if errorlevel 1 (
    echo [ERROR] 找不到 uv 命令。
    echo.
    echo 请先安装 uv，或者确认 uv 已经加入 PATH。
    echo.
    pause
    exit /b 1
)

echo [1/2] 同步开发依赖...
uv sync --group dev

if errorlevel 1 (
    echo.
    echo [ERROR] uv sync --group dev 执行失败。
    echo.
    pause
    exit /b 1
)

echo.
echo [2/2] 使用 PyInstaller 打包...
uv run pyinstaller --clean --noconfirm l30_o20_dashboard.spec

if errorlevel 1 (
    echo.
    echo [ERROR] PyInstaller 打包失败。
    echo.
    pause
    exit /b 1
)

echo.
echo ========================================
echo Windows 可执行文件已生成：
echo   dist\l30-o20-dashboard.exe
echo ========================================
echo.

pause