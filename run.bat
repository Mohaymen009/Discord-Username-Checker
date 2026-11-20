@echo off
rem Double-click to start. Installs the one dependency (aiohttp) the first time.
cd /d "%~dp0"
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
  echo Python is not installed. Get it from https://www.python.org/downloads/ and tick "Add Python to PATH".
  pause
  exit /b 1
)
%PY% -c "import aiohttp" >nul 2>nul || %PY% -m pip install --quiet aiohttp
%PY% checker.py %*
echo.
pause
