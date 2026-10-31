@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
set "install_result=%ERRORLEVEL%"
echo.
pause
exit /b %install_result%
