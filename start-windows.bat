@echo off
REM Double-click this file to run Murimi Farm ERP on your computer.
REM If something goes wrong, this window stays open so you can read the error.
cd /d "%~dp0"

REM Rebuild the private Python box (.venv) if it is missing or broken,
REM for example after moving the folder.
if exist .venv\Scripts\python.exe (
  .venv\Scripts\python.exe -c "import django" >nul 2>&1
  if errorlevel 1 (
    echo The .venv folder is broken, probably because the project was moved. Rebuilding it...
    rmdir /s /q .venv
  )
) else (
  if exist .venv rmdir /s /q .venv
)

if not exist .venv (
  echo Setting up for the first time. This takes about a minute...
  py -m venv .venv 2>nul || python -m venv .venv
  if errorlevel 1 goto :failed
  .venv\Scripts\python.exe -m pip install -r requirements.txt
  if errorlevel 1 goto :failed
)

.venv\Scripts\python.exe manage.py migrate
if errorlevel 1 goto :failed

REM Get today's ZiG and rand rates (carries on if there is no internet).
.venv\Scripts\python.exe manage.py fetch_rates

echo.
echo Murimi Farm ERP is running. Open http://127.0.0.1:8002 in your browser.
echo Staff admin: http://127.0.0.1:8002/admin   (press Ctrl+C here to stop)
echo.
.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8002
goto :end

:failed
echo.
echo Something went wrong. Read the red or last lines above, or send a screenshot.

:end
pause
