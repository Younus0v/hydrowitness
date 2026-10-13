@echo off
rem Double-click this file to start HydroWitness.
setlocal
cd /d "%~dp0"
title HydroWitness

set "PY="
call :try "py -3"
if not defined PY call :try "python"
if not defined PY goto nopython

if not exist ".venv\Scripts\python.exe" (
  echo First run: setting up a private Python environment, about a minute...
  %PY% -m venv .venv
  if errorlevel 1 goto fail
)
if not exist ".venv\.ready" (
  echo Installing the packages HydroWitness needs. This needs internet and happens only on the first run...
  ".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements-run.txt
  if errorlevel 1 goto fail
  echo ok> ".venv\.ready"
)

".venv\Scripts\python.exe" run_local.py %*
if errorlevel 1 goto fail
goto end

:try
rem Ask a candidate for its version. Input is closed (nul) so a Windows helper prompt cannot hang this window.
%~1 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" <nul >nul 2>nul
if not errorlevel 1 set "PY=%~1"
exit /b 0

:nopython
echo.
echo HydroWitness could not find a working Python 3.11 or newer.
echo.
echo If you saw "Python installation manager configuration helper" or "Python was not found":
echo   Open Settings, search for "Manage app execution aliases", and switch OFF every item named
echo   "App Installer python.exe" or "python3.exe". Then switch ON "Python (default)" and
echo   "Python install manager" if they are listed. Close this window and double-click this file again.
echo.
echo If you do not have Python at all:
echo   1. Go to https://www.python.org/downloads/windows/ and install Python 3.13 or newer.
echo   2. On the first installer screen, tick "Add python.exe to PATH".
echo   3. Double-click this file again.
goto end

:fail
echo.
echo Something went wrong. Read the message above, check your internet connection, and try again.

:end
echo.
pause
