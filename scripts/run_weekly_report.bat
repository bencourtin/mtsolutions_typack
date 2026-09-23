@echo off
REM ============================================================
REM ETL Typack - Reporte semanal (Windows Task Scheduler)
REM ============================================================
REM Programado para los lunes. Envia el resumen de la semana:
REM ejecuciones, filas, lineas sin datos y reasignaciones
REM detectadas en el libro de auditoria.
REM ============================================================

setlocal enabledelayedexpansion

for %%I in ("%~dp0..") do set "PROJECT_DIR=%%~fI"
cd /d "%PROJECT_DIR%" || exit /b 1

set "PYTHON_EXE="
for /f "delims=" %%P in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do set "PYTHON_EXE=%%P"
if not defined PYTHON_EXE (
    echo ERROR: no se encontro un interprete de Python real via "py -3".
    echo "python" a secas puede resolver al alias de Microsoft Store en vez de
    echo un interprete real. Instalar el Python Launcher para Windows ^(py.exe,
    echo ya viene con el instalador oficial de python.org^) o verificar el PATH
    echo de la cuenta que corre esta tarea.
    exit /b 1
)

set "VENDOR_DIR=%PROJECT_DIR%\vendor"
if not exist "%VENDOR_DIR%" (
    echo ERROR: no se encontraron las dependencias en "%VENDOR_DIR%"
    echo Instalar con: py -3 -m pip install --target vendor -r requirements\requirements.txt
    exit /b 1
)
set "PYTHONPATH=%VENDOR_DIR%;%PROJECT_DIR%\src;%PROJECT_DIR%"

set "PYTHONIOENCODING=utf-8"

set "LAUNCH_LOG_DIR=%PROJECT_DIR%\logs\launcher"
if not exist "%LAUNCH_LOG_DIR%" mkdir "%LAUNCH_LOG_DIR%"
REM %date% depende del locale del sistema; PowerShell da un formato fijo.
set "HOY=sinfecha"
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd" 2^>nul') do set "HOY=%%i"
set "LAUNCH_LOG=%LAUNCH_LOG_DIR%\weekly_%HOY%.log"

echo [%date% %time%] Generando reporte semanal... >> "%LAUNCH_LOG%"
"%PYTHON_EXE%" "%PROJECT_DIR%\scripts\run_daily_etl.py" --weekly-report >> "%LAUNCH_LOG%" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"

if "%EXIT_CODE%"=="0" (
    echo [%date% %time%] Reporte semanal enviado. >> "%LAUNCH_LOG%"
) else (
    echo [%date% %time%] Error generando el reporte. Codigo: %EXIT_CODE% >> "%LAUNCH_LOG%"
)

exit /b %EXIT_CODE%
