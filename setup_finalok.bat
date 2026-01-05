@echo off
setlocal enabledelayedexpansion

echo === FINALOK setup ===
echo This will install Python (if needed) and required packages.
echo.

set "SCRIPT_DIR=%~dp0"
pushd "%SCRIPT_DIR%"

where py >nul 2>&1
if %errorlevel% neq 0 (
  echo Python launcher not found. Attempting to install Python via winget...
  where winget >nul 2>&1
  if %errorlevel% neq 0 (
    echo ERROR: winget is not available. Please install Python 3.11+ manually.
    echo Download from: https://www.python.org/downloads/windows/
    popd
    exit /b 1
  )
  winget install -e --id Python.Python.3.11 --accept-source-agreements --accept-package-agreements
  if %errorlevel% neq 0 (
    echo ERROR: Python installation failed.
    popd
    exit /b 1
  )
)

where py >nul 2>&1
if %errorlevel% neq 0 (
  echo ERROR: Python launcher still not found after install.
  popd
  exit /b 1
)

echo.
echo Upgrading pip...
py -3 -m pip install --upgrade pip
if %errorlevel% neq 0 (
  echo ERROR: Failed to upgrade pip.
  popd
  exit /b 1
)

echo.
echo Installing requirements...
py -3 -m pip install -r requirements.txt
if %errorlevel% neq 0 (
  echo ERROR: Failed to install requirements.
  popd
  exit /b 1
)

echo.
echo Setup complete. You can now run FINALOK.py.
popd
endlocal
