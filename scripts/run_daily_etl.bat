@echo off
REM ============================================================
REM ETL Typack - Ejecucion diaria (Windows Task Scheduler)
REM ============================================================
REM Extraccion + transformacion + publicacion de la auditoria.
REM
REM Cuatro cosas que este lanzador resuelve y que la version
REM anterior no:
REM   1. La ruta del proyecto sale del propio .bat (%~dp0..), no
REM      hardcodeada al perfil de un usuario.
REM   2. Resuelve el interprete real via "py -3", no "python" a
REM      secas: en muchas instalaciones de Windows "python" apunta
REM      al alias-stub de Microsoft Store (WindowsApps\python.exe),
REM      que no es un interprete y sale con el codigo 9009 sin
REM      ejecutar nada. "py" es el lanzador oficial y no cae en esa
REM      trampa.
REM   3. Con PYTHONPATH apuntando a las dependencias instaladas en
REM      vendor\ (pip install --target, sin venv) y al codigo propio
REM      (src\ y la raiz del repo). Sin esto, una tarea programada
REM      bajo otra cuenta resuelve el interprete pero sin el paquete
REM      instalado, y falla con ModuleNotFoundError.
REM   4. Guarda la salida. Antes se descartaba: si el proceso
REM      moria antes de configurar el logger, no quedaba rastro.
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
