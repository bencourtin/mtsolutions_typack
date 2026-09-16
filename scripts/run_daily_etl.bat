@echo off
REM ============================================================
REM ETL Typack - Ejecucion diaria (Windows Task Scheduler)
REM ============================================================
REM Extraccion + transformacion + publicacion de la auditoria.
REM
REM Tres cosas que este lanzador resuelve y que la version
REM anterior no:
REM   1. La ruta del proyecto sale del propio .bat (%~dp0..), no
REM      hardcodeada al perfil de un usuario.
REM   2. Usa el Python del entorno virtual. Con "python" a secas,
REM      una tarea programada bajo otra cuenta resuelve al Python
REM      del sistema, sin el paquete instalado, y falla.
REM   3. Guarda la salida. Antes se descartaba: si el proceso
REM      moria antes de configurar el logger, no quedaba rastro.
REM ============================================================

setlocal enabledelayedexpansion

for %%I in ("%~dp0..") do set "PROJECT_DIR=%%~fI"
cd /d "%PROJECT_DIR%" || exit /b 1

set "PYTHON_EXE=%PROJECT_DIR%\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    echo ERROR: no se encontro el entorno virtual en "%PYTHON_EXE%"
    echo Crear con: python -m venv .venv ^&^& .venv\Scripts\python -m pip install -e .
    exit /b 1
)

REM Sin esto, los acentos de los mensajes rompen la salida en cp1252.
set "PYTHONIOENCODING=utf-8"

REM Bitacora de ultimo recurso, local al proyecto: si falla algo
REM antes de que Python resuelva PATH_LOGS, aca queda el motivo.
set "LAUNCH_LOG_DIR=%PROJECT_DIR%\logs\launcher"
if not exist "%LAUNCH_LOG_DIR%" mkdir "%LAUNCH_LOG_DIR%"
REM %date% depende del locale del sistema; PowerShell da un formato fijo.
set "HOY=sinfecha"
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd" 2^>nul') do set "HOY=%%i"
set "LAUNCH_LOG=%LAUNCH_LOG_DIR%\daily_%HOY%.log"

echo [%date% %time%] Iniciando ETL Typack... >> "%LAUNCH_LOG%"
"%PYTHON_EXE%" "%PROJECT_DIR%\scripts\run_daily_etl.py" >> "%LAUNCH_LOG%" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"

if "%EXIT_CODE%"=="0" (
    echo [%date% %time%] ETL completado correctamente. >> "%LAUNCH_LOG%"
) else if "%EXIT_CODE%"=="2" (
    echo [%date% %time%] Ya habia otra corrida en curso: se omitio esta. >> "%LAUNCH_LOG%"
) else (
    echo [%date% %time%] ETL finalizado con errores. Codigo: %EXIT_CODE% >> "%LAUNCH_LOG%"
)

exit /b %EXIT_CODE%
