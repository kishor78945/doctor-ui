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
echo   STEP 1: Installing Dependencies
echo ==========================================
"%PYTHON%" -m pip install -r requirements_desktop.txt
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Failed to install dependencies!
    echo Please make sure you have internet access.
    pause
    exit /b
)

echo.
echo ==========================================
echo   STEP 2: Building Executable
echo ==========================================
echo This process may take 1-2 minutes...
echo.

"%PYTHON%" -m PyInstaller --noconsole --onefile ^
    --name "DischargeGenerator" ^
    --add-data "templates;templates" ^
    --collect-all whisper ^
    --hidden-import=engineio.async_drivers.threading ^
    desktop_app.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Build failed!
    echo Please check the error messages above.
    pause
    exit /b
)

echo.
echo ==========================================
echo   BUILD SUCCESSFUL!
echo ==========================================
echo.
echo You can find your app here:
echo %~dp0dist\DischargeGenerator.exe
echo.
pause
