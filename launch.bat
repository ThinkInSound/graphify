@echo off
setlocal
cd /d "%~dp0"

:: ── Check Python ─────────────────────────────────────────────────────────────
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo  ERROR: Python was not found in your PATH.
    echo  Please install Python 3.8+ from https://python.org
    echo.
    pause
    exit /b 1
)

:: ── Check core dependencies ───────────────────────────────────────────────────
python -c "import customtkinter, pyvis" >nul 2>&1
if %errorlevel% neq 0 (
    echo  Installing required packages...
    pip install customtkinter pyvis tkinterdnd2 --quiet
    if %errorlevel% neq 0 (
        echo.
        echo  ERROR: Package installation failed.
        echo  Try running manually:
        echo      pip install customtkinter pyvis tkinterdnd2
        echo.
        pause
        exit /b 1
    )
)

:: ── Launch — terminal stays open until the window is visible ─────────────────
python _launch_helper.py

endlocal
