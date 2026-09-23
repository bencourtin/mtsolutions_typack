"""
Script orquestador para ejecución diaria del ETL Typack.

Este script coordina la extracción y transformación de datos,
gestiona logs y envía notificaciones en caso de error.

Uso:
    python scripts/run_daily_etl.py
    python scripts/run_daily_etl.py --weekly-report  # Genera reporte semanal

Configuración:
    - .env: Rutas de archivos, credenciales y email
"""

import csv
import json
import subprocess
import sys
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
import argparse
import traceback

from scripts.notify import send_error_alert, send_weekly_report
from scripts.stats_tracker import StatsTracker
from config.settings import (
    ETL_MAX_LINEAS_FALLIDAS,
    EXTRACTOR_WINDOW_DAYS,
    PATHS,
    RETENCION_LOGS_DIAS,
)
from typack_etl.audit.publicar import publicar

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Estados de línea que no impiden continuar pero merecen quedar en el log.
#
# `RELOJ_INESPERADO` no aborta a propósito: los datos siguen siendo los que el
# proveedor mandó y el 99 % de la ventana está bien. Lo que avisa es que el
# modelo temporal dejó de describir a su servidor, y eso se decide mirando,
# no cortando la publicación del día.
ESTADOS_DE_AVISO = ("SIN_DATOS", "COBERTURA_BAJA", "TRUNCADO_SOSPECHOSO", "RELOJ_INESPERADO")

# Marca de corrida en curso. Vive en PATH_LOGS junto al resto de las bitácoras.
LOCK_FILENAME = "etl.lock"

# A partir de acá se considera que el proceso que dejó el lock murió sin
# limpiarlo. Los timeouts internos suman 3 h (1 h extractor + 2 h transformer),
# así que 5 h no puede ser una corrida viva.
LOCK_ANTIGUEDAD_HORAS = 5

# Código de salida propio para "ya hay otra corrida": no es un fallo del ETL, y
# conviene distinguirlo del 1 en el historial del Task Scheduler.
SALIDA_YA_EN_CURSO = 2


class CorridaEnCurso(Exception):
    """Otra corrida tiene tomado el lock."""


@contextmanager
def corrida_unica(logger: logging.Logger):
    """Impide que dos corridas se pisen sobre el mismo estado.

    El estado (libro de auditoría, cobertura, `ultimas_corridas.json`) son CSV
    sin bloqueo sobre una carpeta sincronizada: dos procesos escribiéndolo a la
    vez lo corrompen. El caso real no es el Task Scheduler —que ya sabe no
    relanzar— sino una recarga histórica lanzada a mano mientras corre la tarea.

    La creación del archivo es atómica (`O_CREAT | O_EXCL`), así que la carrera
    entre comprobar y crear no existe.
    """
    path = Path(PATHS.get("logs", "logs")) / LOCK_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        try:
            edad = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
        except OSError:
            edad = timedelta(0)
        if edad > timedelta(hours=LOCK_ANTIGUEDAD_HORAS):
            logger.warning(
                f"Se descarta un lock de {edad.total_seconds() / 3600:.1f} h: "
                "la corrida que lo dejó no puede seguir viva."
            )
            path.unlink(missing_ok=True)

    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        contenido = ""
        try:
            contenido = path.read_text(encoding="utf-8").strip()
        except OSError:
            pass
        raise CorridaEnCurso(contenido or str(path))

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(f"pid={os.getpid()} inicio={datetime.now().isoformat(timespec='seconds')}\n")
        yield path
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError as e:
            # Un lock que no se puede borrar bloquearía las corridas siguientes
            # hasta que venza por antigüedad. Hay que verlo en el log.
            logger.error(f"No se pudo liberar el lock {path}: {e}")


def avisar(logger: logging.Logger, enviado: bool) -> bool:
    """Deja constancia cuando una alerta por correo no sale.

    `notify._send_email` se traga cualquier fallo de SMTP y devuelve False sin
    alterar el código de salida: un ETL que falla y no puede avisar era
    indistinguible de uno que anda bien. Al menos que quede en el log.
    """
    if not enviado:
        logger.error(
            "No se pudo enviar la alerta por correo. Revisar SMTP_* y "
            "EMAIL_RECIPIENTS_ERROR en el .env."
        )
    return enviado


def rotar_logs_scheduler(dias: int) -> int:
    """Borra los `etl_YYYYMMDD.log` más viejos que `dias`.

    `io.rotar_logs` sólo cubre los `plan_`/`results_` del extractor y no mira
    subcarpetas, así que el log del orquestador crecía sin límite: un archivo
    por día, para siempre. Retorna cuántos borró.
    """
    directorio = Path(PATHS.get("logs", "logs")) / "scheduler"
    if dias <= 0 or not directorio.exists():
        return 0

    limite = datetime.now() - timedelta(days=dias)
    borrados = 0
    for archivo in directorio.glob("etl_*.log"):
        try:
            cuando = datetime.strptime(archivo.stem.removeprefix("etl_"), "%Y%m%d")
        except ValueError:
            continue  # nombre inesperado: no se toca
        if cuando < limite:
            try:
                archivo.unlink()
                borrados += 1
            except OSError:
                pass
    return borrados


def leer_ultimo_resultado(logs_dir=None):
    """Resume el results_*.csv más reciente que dejó el extractor.

    Es la fuente de verdad sobre cómo fue la corrida: cuántas líneas
    respondieron, cuántas filas trajeron y qué estado quedó en cada una.
    Retorna None si no hay ninguno.

    `logs_dir` permite mirar otro directorio que el de producción: la recarga
    histórica le da a cada mes el suyo para poder retomarla donde quedó.
    """
    logs_dir = Path(logs_dir) if logs_dir else Path(PATHS.get("logs", "logs"))
    archivos = sorted(logs_dir.glob("results_*.csv"))
    if not archivos:
        return None

    with archivos[-1].open(encoding="utf-8-sig", newline="") as f:
        filas = list(csv.DictReader(f, delimiter=";"))
    if not filas:
        return None

    def entero(valor):
        try:
            return int(valor)
        except (TypeError, ValueError):
            return 0

    fallidas = [r for r in filas if r.get("estado") == "ERROR" or r.get("success") == "0"]
    alertas = [
        f"{r['line']}: {r.get('estado')} ({r.get('error') or r.get('cobertura_pct', '')}%)".replace("(%)", "")
        for r in filas
        if r.get("estado") in ESTADOS_DE_AVISO
    ]
    alertas += [f"{r['line']}: ERROR {r.get('error', '')[:120]}" for r in fallidas]

    return {
        "archivo": archivos[-1],
        "total": len(filas),
        "ok": len(filas) - len(fallidas),
        "fallidas": len(fallidas),
        "filas": sum(entero(r.get("rows")) for r in filas),
        "alertas": alertas,
        "lineas_fallidas": [r["line"] for r in fallidas],
    }


def contar_consolidado() -> int:
    """Filas del consolidado publicado, sin cargarlo entero en memoria."""
    destino = Path(PATHS.get("consolidated", "data/consolidated")) / "mt_typ_consolidated.csv"
    if not destino.exists():
        return 0
    try:
        with destino.open(encoding="utf-8-sig", newline="") as f:
            return max(sum(1 for _ in f) - 1, 0)  # menos la cabecera
    except OSError:
        return 0


def marcar_consolidado(estado: str, detalle: dict) -> None:
    """Deja junto al consolidado un archivo con el estado de la corrida.

    Power BI lee el CSV; este sidecar existe para que una persona pueda saber
    si lo que está mirando salió de una corrida completa o parcial, sin tener
    que cambiar el esquema que consume el reporte.
    """
    destino = Path(PATHS.get("consolidated", "data/consolidated")) / "mt_typ_consolidated.estado.json"
    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        with destino.open("w", encoding="utf-8") as f:
            json.dump({"estado": estado, **detalle}, f, ensure_ascii=False, indent=2)
    except OSError:
        pass  # no vale la pena tumbar el ETL por el sidecar


def publicar_auditoria(logger: logging.Logger) -> dict:
    """Publica el libro y la cobertura junto al consolidado.

    Se llama sólo con el consolidado ya escrito. Publicar reasignaciones
    frescas al lado de un consolidado que no se pudo actualizar mostraría en el
    BI cambios sobre datos viejos, así que la decisión vive acá: el orquestador
    es el único que sabe cómo terminó la corrida.

    Un fallo al publicar no tumba el ETL —el consolidado ya está en su lugar y
    es lo que el reporte necesita— pero queda en el log y en el sidecar, para
    que una tabla de auditoría estancada no pase inadvertida.
    """
    try:
        resumen = publicar(
            Path(PATHS.get("audit", "data/audit")),
            Path(PATHS.get("consolidated", "data/consolidated")),
        )
        logger.info(
            f"Auditoría publicada. Reasignaciones: {resumen['filas_reasignaciones']} | "
            f"cobertura: {resumen['filas_cobertura']} día(s)"
        )
        return resumen
    except (OSError, ValueError, KeyError) as e:
        logger.warning(f"No se pudo publicar la auditoría junto al consolidado: {e}")
        return {}


def setup_logging() -> logging.Logger:
    """Configura el sistema de logging."""
    logs_path = PATHS.get("logs", "logs")
    
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
    Ejecuta el extractor de ventana móvil.

    La ventana móvil vuelve a consultar los últimos EXTRACTOR_WINDOW_DAYS
    días, de modo que una detención reclasificada después del cierre de mes sí
    se re-extrae. Las recargas de meses cerrados van por
    `scripts/recarga_historica.py`.

    Returns:
        tuple: (success, rows_extracted, error_message)
    """
    logger.info(f"Iniciando extracción de datos (ventana móvil de {EXTRACTOR_WINDOW_DAYS} días)...")

    try:
        # Configurar encoding UTF-8 para Windows
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"

        result = subprocess.run(
            [
                sys.executable, "-m",
                "src.typack_etl.extractor.pipelines.extractor_30m",
                "--no-progress",  # la salida se captura: la barra sólo ensucia el log
            ],
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

        # El conteo y el estado salen del results_*.csv, no de parsear stdout:
        # un cambio de redacción en un print rompía la métrica en silencio.
        resumen = leer_ultimo_resultado()
        if resumen is None:
            error_msg = "El extractor no dejó results_*.csv; no se puede verificar la corrida"
            logger.error(error_msg)
            return False, 0, error_msg

        rows, fallidas, alertas = resumen["filas"], resumen["fallidas"], resumen["alertas"]
        logger.info(
            f"Extractor completado. Filas: {rows} | líneas OK: {resumen['ok']}/{resumen['total']}"
        )
        for aviso in alertas:
            logger.warning(f"  {aviso}")

        if fallidas > ETL_MAX_LINEAS_FALLIDAS:
            error_msg = (
                f"{fallidas} líneas fallaron (máximo tolerado: {ETL_MAX_LINEAS_FALLIDAS}). "
                "No se transforma para no publicar un consolidado incompleto."
            )
            logger.error(error_msg)
            return False, rows, error_msg

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
            [sys.executable, "-m", "src.typack_etl.transformer.transformer", "--incremental"],
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
        
        # Se cuenta el consolidado que quedó en disco, que es el dato real.
        # Antes esto devolvía siempre 0 y el reporte semanal informaba
        # "0 filas transformadas" todas las semanas.
        rows = contar_consolidado()

        logger.info(f"Transformer completado. Filas en el consolidado: {rows}")
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
    """Ejecuta el ETL diario, garantizando que no haya dos corridas a la vez."""
    logger = setup_logging()
    try:
        with corrida_unica(logger):
            return _ejecutar_etl(logger)
    except CorridaEnCurso as detalle:
        logger.warning(
            f"Ya hay una corrida en curso ({detalle}). Se omite esta ejecución "
            "para no corromper el estado compartido."
        )
        return SALIDA_YA_EN_CURSO


def _ejecutar_etl(logger: logging.Logger):
    """El ETL propiamente dicho: extracción, transformación y publicación."""
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

    # Se rota al principio: si la corrida aborta en la extracción, el log de
    # hace tres meses igual se limpia.
    borrados = rotar_logs_scheduler(RETENCION_LOGS_DIAS)
    if borrados:
        logger.info(f"Bitácoras del orquestador rotadas: {borrados} archivo(s)")

    # Paso 1: Extracción
    ext_success, rows_extracted, ext_error = run_extractor(logger)
    
    if not ext_success:
        overall_success = False
        errors.append(f"Extractor: {ext_error[:200]}")

        avisar(logger, send_error_alert(
            error_type="extractor",
            error_message=ext_error,
            details={
                "Fecha de ejecución": execution_start.strftime('%Y-%m-%d %H:%M:%S'),
                "Filas extraídas antes del error": rows_extracted
            }
        ))

        # No se transforma: hacerlo publicaría un consolidado a partir de datos
        # que no se pudieron actualizar, sin que nadie lo note aguas abajo.
        logger.error("Extracción fallida. Se omite la transformación para no publicar datos incompletos.")
        marcar_consolidado("EXTRACCION_FALLIDA", {
            "fecha": execution_start.strftime('%Y-%m-%d %H:%M:%S'),
            "detalle": ext_error[:500],
            "consolidado_actualizado": False,
        })
        tracker.record_execution(
            success=False,
            rows_extracted=rows_extracted,
            rows_transformed=0,
            duration_seconds=(datetime.now() - execution_start).total_seconds(),
            errors=errors,
            lines_without_data=(leer_ultimo_resultado() or {}).get("lineas_fallidas", []),
        )
        logger.info("=" * 60)
        logger.error("❌ ETL DIARIO ABORTADO EN LA EXTRACCIÓN")
        logger.info("=" * 60)
        return 1

    # Paso 2: Transformación
    trans_success, rows_transformed, trans_error = run_transformer(logger)

    if not trans_success:
        overall_success = False
        errors.append(f"Transformer: {trans_error[:200]}")
        
        # Enviar alerta de error inmediata
        avisar(logger, send_error_alert(
            error_type="transformer",
            error_message=trans_error,
            details={
                "Fecha de ejecución": execution_start.strftime('%Y-%m-%d %H:%M:%S'),
                "Filas extraídas (previas)": rows_extracted
            }
        ))
    
    # Paso 3: Publicación de la auditoría junto al consolidado
    auditoria = publicar_auditoria(logger) if trans_success else {}

    # Registrar estadísticas de esta ejecución
    execution_end = datetime.now()
    duration_seconds = (execution_end - execution_start).total_seconds()

    resumen = leer_ultimo_resultado() or {}
    parcial = bool(resumen.get("fallidas"))
    marcar_consolidado(
        "PARCIAL" if parcial else ("OK" if overall_success else "TRANSFORMACION_FALLIDA"),
        {
            "fecha": execution_end.strftime('%Y-%m-%d %H:%M:%S'),
            "lineas_totales": resumen.get("total"),
            "lineas_ok": resumen.get("ok"),
            "lineas_fallidas": resumen.get("lineas_fallidas", []),
            "filas_extraidas": rows_extracted,
            "consolidado_actualizado": trans_success,
            "auditoria_publicada": bool(auditoria),
            "reasignaciones_publicadas": auditoria.get("filas_reasignaciones"),
        },
    )
    if parcial:
        logger.warning(
            f"Consolidado publicado con {resumen['fallidas']} línea(s) sin actualizar: "
            f"{', '.join(resumen.get('lineas_fallidas', []))}"
        )

    # `lineas_fallidas` alimenta el bloque "líneas sin datos" del reporte
    # semanal. Antes no se pasaba, así que el reporte afirmaba todas las
    # semanas que todas las líneas habían tenido datos.
    tracker.record_execution(
        success=overall_success,
        rows_extracted=rows_extracted,
        rows_transformed=rows_transformed,
        duration_seconds=duration_seconds,
        errors=errors,
        lines_without_data=resumen.get("lineas_fallidas", []),
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
