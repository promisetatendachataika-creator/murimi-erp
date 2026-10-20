@echo off
REM Double-click if you forgot your Murimi Farm ERP password (on this computer).
REM It gives your account a temporary password; you choose a new one when you log in.
cd /d "%~dp0"
.venv\Scripts\python.exe manage.py migrate -v 0
.venv\Scripts\python.exe manage.py reset_password
pause
