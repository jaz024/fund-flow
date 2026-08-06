@echo off
setlocal
cd /d "%~dp0"

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-windows.ps1"
set "START_EXIT_CODE=%ERRORLEVEL%"

if not "%START_EXIT_CODE%"=="0" (
  echo.
  echo The app could not be started. Please read the message above.
  echo If you need help, take a screenshot of this window.
  pause
)

exit /b %START_EXIT_CODE%
