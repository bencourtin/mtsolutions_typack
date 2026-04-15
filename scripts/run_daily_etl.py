"""
Script orquestador para ejecución diaria del ETL Typack.

Este script coordina la extracción y transformación de datos,
gestiona logs y envía notificaciones en caso de error.

Uso:
    python scripts/run_daily_etl.py
    python scripts/run_daily_etl.py --weekly-report  # Genera reporte semanal

Configuración:
    - config/config.json: Rutas de archivos
    - config/email_config.json: Configuración de notificaciones
"""

import subprocess
import sys
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
import argparse
import traceback

# Asegurar que el directorio raíz del proyecto esté en el path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

# Importar después de configurar el path
from scripts.notify import send_error_alert, send_weekly_report
from scripts.stats_tracker import StatsTracker


def _load_config() -> dict:
    """Carga configuración principal desde config/config.json."""
    try:
        config_path = PROJECT_ROOT / "config" / "config.json"
        with config_path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error cargando config.json: {e}")
        return {}


def setup_logging() -> logging.Logger:
    """Configura el sistema de logging."""
    config = _load_config()
    logs_path = config.get("paths", {}).get("logs", "logs")
    
    # Crear directorio de logs si no existe
    log_dir = Path(logs_path) / "scheduler"
    log_dir.mkdir(parents=True, exist_ok=True)
    
    # Archivo de log diario
    log_file = log_dir / f"etl_{datetime.now().strftime('%Y%m%d')}.log"
    
    # Configurar logger
    logger = logging.getLogger("etl_scheduler")
    logger.setLevel(logging.INFO)
    
    # Handler para archivo
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    
    # Handler para consola
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    
    # Formato
    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    
    # Limpiar handlers previos
    logger.handlers.clear()
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

def run_extractor(logger: logging.Logger) -> tuple[bool, int, str]:
    """
    Ejecuta el extractor mensual.
    
    Returns:
        tuple: (success, rows_extracted, error_message)
    """
    logger.info("Iniciando extracción de datos...")
    
    try:
        # Configurar encoding UTF-8 para Windows
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        
        result = subprocess.run(
            [sys.executable, "-m", "src.typack_etl.extractor.pipelines.extractor_mensual"],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
            timeout=3600,  # 1 hora máximo
            env=env,
            encoding="utf-8",
            errors="replace"  # Reemplaza caracteres no soportados
        )
        
        output = result.stdout + result.stderr
        
        if result.returncode != 0:
            error_msg = f"Exit code: {result.returncode}\n{output}"
            logger.error(f"Error en extractor: {error_msg}")
            return False, 0, error_msg
        
        # Intentar extraer número de filas del output
        rows = 0
        if "Total filas acumuladas:" in output:
            try:
                rows = int(output.split("Total filas acumuladas:")[-1].strip().split()[0])
            except:
                pass
        
        logger.info(f"Extractor completado exitosamente. Filas: {rows}")
        return True, rows, ""
        
    except subprocess.TimeoutExpired:
        error_msg = "Timeout: La extracción excedió el tiempo límite de 1 hora"
        logger.error(error_msg)
        return False, 0, error_msg
        
    except Exception as e:
        error_msg = f"Excepción en extractor: {str(e)}\n{traceback.format_exc()}"
        logger.error(error_msg)
        return False, 0, error_msg


def run_transformer(logger: logging.Logger) -> tuple[bool, int, str]:
    """
    Ejecuta el transformador.
    
    Returns:
        tuple: (success, rows_transformed, error_message)
    """
    logger.info("Iniciando transformación de datos...")
    
    try:
        # Configurar encoding UTF-8 para Windows
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        
        result = subprocess.run(
            [sys.executable, "-m", "src.typack_etl.transformer.transformer"],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
            timeout=7200,  # 2 horas máximo
            env=env,
            encoding="utf-8",
            errors="replace"  # Reemplaza caracteres no soportados
        )
        
        output = result.stdout + result.stderr
        
        if result.returncode != 0:
            error_msg = f"Exit code: {result.returncode}\n{output}"
            logger.error(f"Error en transformer: {error_msg}")
            return False, 0, error_msg
        
        # Intentar extraer información del output
        rows = 0
        # Buscar patrones comunes de output
        
        logger.info(f"Transformer completado exitosamente.")
        return True, rows, ""
        
    except subprocess.TimeoutExpired:
        error_msg = "Timeout: La transformación excedió el tiempo límite de 2 horas"
        logger.error(error_msg)
        return False, 0, error_msg
        
    except Exception as e:
        error_msg = f"Excepción en transformer: {str(e)}\n{traceback.format_exc()}"
        logger.error(error_msg)
        return False, 0, error_msg


def run_daily_etl():
    """Ejecuta el proceso ETL diario completo."""
    logger = setup_logging()
    tracker = StatsTracker()
    
    logger.info("=" * 60)
    logger.info("INICIO ETL DIARIO")
    logger.info(f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)
    
    execution_start = datetime.now()
    overall_success = True
    errors = []
    rows_extracted = 0
    rows_transformed = 0
    
    # Paso 1: Extracción
    ext_success, rows_extracted, ext_error = run_extractor(logger)
    
    if not ext_success:
        overall_success = False
        errors.append(f"Extractor: {ext_error[:200]}")
        
        # Enviar alerta de error inmediata
        send_error_alert(
            error_type="extractor",
            error_message=ext_error,
            details={
                "Fecha de ejecución": execution_start.strftime('%Y-%m-%d %H:%M:%S'),
                "Filas extraídas antes del error": rows_extracted
            }
        )
        logger.warning("Extractor falló. Continuando con transformer de datos existentes...")
    
    # Paso 2: Transformación (intentar aunque falle extracción)
    trans_success, rows_transformed, trans_error = run_transformer(logger)
    
    if not trans_success:
        overall_success = False
        errors.append(f"Transformer: {trans_error[:200]}")
        
        # Enviar alerta de error inmediata
        send_error_alert(
            error_type="transformer", 
            error_message=trans_error,
            details={
                "Fecha de ejecución": execution_start.strftime('%Y-%m-%d %H:%M:%S'),
                "Filas extraídas (previas)": rows_extracted
            }
        )
    
    # Registrar estadísticas de esta ejecución
    execution_end = datetime.now()
    duration_seconds = (execution_end - execution_start).total_seconds()
    
    tracker.record_execution(
        success=overall_success,
        rows_extracted=rows_extracted,
        rows_transformed=rows_transformed,
        duration_seconds=duration_seconds,
        errors=errors
    )
    
    # Log final
    logger.info("=" * 60)
    if overall_success:
        logger.info("✅ ETL DIARIO COMPLETADO EXITOSAMENTE")
    else:
        logger.error("❌ ETL DIARIO COMPLETADO CON ERRORES")
    
    logger.info(f"Duración total: {duration_seconds/60:.1f} minutos")
    logger.info(f"Filas extraídas: {rows_extracted}")
    logger.info(f"Filas transformadas: {rows_transformed}")
    logger.info("=" * 60)
    
    return 0 if overall_success else 1


def generate_weekly_report():
    """Genera y envía el reporte semanal."""
    logger = setup_logging()
    logger.info("Generando reporte semanal...")
    
    tracker = StatsTracker()
    stats = tracker.get_weekly_stats()
    
    if stats:
        success = send_weekly_report(stats)
        if success:
            logger.info("Reporte semanal enviado exitosamente.")
        else:
            logger.error("Error enviando reporte semanal.")
        return 0 if success else 1
    else:
        logger.warning("No hay estadísticas disponibles para el reporte semanal.")
        return 1


def parse_args():
    """Parsea argumentos de línea de comandos."""
    parser = argparse.ArgumentParser(
        description="Orquestador ETL Typack - Ejecución diaria automatizada"
    )
    parser.add_argument(
        "--weekly-report",
        action="store_true",
        help="Genera y envía el reporte semanal en lugar de ejecutar el ETL"
    )
    parser.add_argument(
        "--test-email",
        action="store_true", 
        help="Envía un email de prueba para verificar la configuración"
    )
    return parser.parse_args()


def main():
    """Punto de entrada principal."""
    args = parse_args()
    
    os.chdir(PROJECT_ROOT)
    
    if args.test_email:
        from scripts.notify import send_test_email
        success = send_test_email()
        return 0 if success else 1
    
    if args.weekly_report:
        return generate_weekly_report()
    
    return run_daily_etl()


if __name__ == "__main__":
    sys.exit(main())
