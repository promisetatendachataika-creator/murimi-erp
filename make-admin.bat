@echo off
REM Double-click to let your account into the staff admin page (http://127.0.0.1:8002/admin).
cd /d "%~dp0"
.venv\Scripts\python.exe manage.py make_admin
pause
