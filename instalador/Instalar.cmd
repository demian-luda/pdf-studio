@echo off
rem Instala PDF Studio para este usuario (sin permisos de administrador).
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar.ps1" %*
echo.
pause
