@echo off
setlocal

:: Set path to venv python
set "VENV_PYTHON=%~dp0.venv\Scripts\python.exe"

if exist "%VENV_PYTHON%" (
    echo [INFO] Found virtual environment at: %VENV_PYTHON%
    set "PYTHON=%VENV_PYTHON%"
) else (
    echo [INFO] Virtual environment not found, forcing system 'python'...
    set "PYTHON=python"
)

echo.
echo ==========================================
echo   BUILDING DEBUG VERSION (Console Enabled)
echo ==========================================
echo.

"%PYTHON%" -m PyInstaller --onefile ^
    --name "DischargeGenerator_Debug" ^
    --add-data "templates;templates" ^
    --collect-all whisper ^
    --hidden-import=engineio.async_drivers.threading ^
    desktop_app.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Build failed!
    pause
    exit /b
)

echo.
echo ==========================================
echo   DEBUG BUILD SUCCESSFUL!
echo ==========================================
echo.
echo Run 'dist\DischargeGenerator_Debug.exe' and check the console output.
pause
