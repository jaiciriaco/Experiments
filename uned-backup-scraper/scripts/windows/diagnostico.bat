@echo off
setlocal
cd /d "%~dp0\..\.."
if not exist .venv\Scripts\python.exe (
  echo El entorno virtual no existe. Ejecuta instalar.bat
  pause
  exit /b 1
)
.venv\Scripts\python.exe -m uned_backup doctor --config config.json
echo.
pause
