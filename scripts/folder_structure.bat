@echo off
REM === Nombre de la carpeta raíz del proyecto ===
set "PROJECT_ROOT=MTSOLUTIONS_TYPACK"

REM Crear carpeta raíz y entrar en ella
mkdir "%PROJECT_ROOT%"
pushd "%PROJECT_ROOT%"

REM Carpetas de nivel raíz
mkdir ".venv"
mkdir "config"
mkdir "data"
mkdir "logs"
mkdir "notebooks"
mkdir "scripts"
mkdir "docker"
mkdir "requirements"
mkdir "src"
mkdir "tests"

REM Subcarpetas de data
mkdir "data\raw"
mkdir "data\processed"
mkdir "data\external"

REM Carpetas principales del paquete Python
mkdir "src\mtsolutions_typack"
mkdir "src\mtsolutions_typack\extractor"
mkdir "src\mtsolutions_typack\transformer"

REM Subcarpetas de extractor
mkdir "src\mtsolutions_typack\extractor\data"
mkdir "src\mtsolutions_typack\extractor\functions"
mkdir "src\mtsolutions_typack\extractor\pipelines"
mkdir "src\mtsolutions_typack\extractor\services"
mkdir "src\mtsolutions_typack\extractor\utils"

REM Subcarpetas de transformer
mkdir "src\mtsolutions_typack\transformer\pipelines"
mkdir "src\mtsolutions_typack\transformer\utils"

REM Listado rápido
echo.
echo Directorios creados bajo %CD%:
echo.
tree /F

REM Volver a la carpeta original
popd

echo.
echo Estructura de proyecto creada correctamente.
pause
