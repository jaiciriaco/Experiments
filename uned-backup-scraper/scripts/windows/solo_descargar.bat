@echo off
setlocal
cd /d "%~dp0\..\.."
if not exist .venv\Scripts\python.exe (
  echo Primero debes ejecutar instalar.bat
  pause
  exit /b 1
)
.venv\Scripts\python.exe -m uned_backup backup --config config.json
echo.
pause
