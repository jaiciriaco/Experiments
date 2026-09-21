@echo off
setlocal
cd /d "%~dp0\..\.."

where py >nul 2>nul
if errorlevel 1 (
  echo No se ha encontrado Python.
  echo Instala Python 3.11 o superior desde https://www.python.org/downloads/windows/
  echo Durante la instalacion marca "Add Python to PATH".
  pause
  exit /b 1
)

if not exist config.json copy /Y config.example.json config.json >nul

echo Creando entorno virtual...
py -3 -m venv .venv
if errorlevel 1 goto :error

call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
if errorlevel 1 goto :error
python -m pip install -e .
if errorlevel 1 goto :error
set PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT=120000
python -m playwright install chromium
if errorlevel 1 goto :error

echo.
echo Instalacion completada. Ahora ejecuta ejecutar_todo.bat
pause
exit /b 0

:error
echo.
echo La instalacion no se ha completado. Revisa los mensajes anteriores.
pause
exit /b 1
