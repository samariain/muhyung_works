@echo off
cd /d "%~dp0"
title KPI Dashboard Server (auto-restart)
echo.
echo  KPI Dashboard Server - auto restart mode
echo  Keep this window open. Close it to stop completely.
echo  Open http://localhost:8000 in your browser.
echo.
:loop
echo  [%date% %time%] starting server...
where python >nul 2>nul
if %errorlevel%==0 (
  python server.py
) else (
  py server.py
)
echo.
echo  [%date% %time%] server stopped. restarting in 3 seconds...
echo  (To stop for good, close this window now.)
timeout /t 3 /nobreak >nul
goto loop
