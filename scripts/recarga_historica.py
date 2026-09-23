"""
Recarga histórica: reescribe la base con las estructuras auditables.

Por qué existe
--------------
La base en producción —308 CSV, mayo 2024 → hoy— está escrita con el formato
viejo: sin `record_key`, y los 88 archivos de 2024 ni siquiera tienen la
columna `order`. Sin `record_key` el consolidado no puede unirse con el libro
de auditoría, así que la trazabilidad del BI existe sólo para los últimos 35
días. Este script vuelve a bajar todo el rango con el extractor nuevo.

Reemplazó a `run_historico_2025_2026.bat` y al `extractor_mensual`, borrados
el 2026-09-21: cubrían un rango incompleto, usaban medianoche en vez del corte
operativo y no estampaban traza ni auditaban.

Por qué NO se usa `--no-audit`
------------------------------
Sería lo intuitivo para no ensuciar el libro con cambios falsos, pero apaga de
más: en `extractor_30m`, `traza.anotar()` corre sólo `if ctx is not None`, y
`--no-audit` deja `ctx = None`. Una recarga con esa bandera escribiría los 308
archivos **sin `record_key`**, que es justo lo que se busca arreglar.

La vía correcta es otra:

  1. **El destino tiene que estar vacío.** Con las particiones vacías,
     `previas = []` y el CDC clasifica todo como `NUEVO`; como `cdc.comparar`
     usa `registrar_nuevos=False`, las altas no se escriben al libro. Sin
     archivo viejo contra el cual comparar no hay `REASIGNACION` inventada.
     Recargar **encima** de la base vieja marcaría `reasignada=1` en miles de
     filas por simples diferencias de formato, y eso viajaría al consolidado.
     Por eso el script se niega a arrancar sobre un destino sucio.
  2. **Auditoría encendida, pero con `--audit-dir` desechable.** Así el
     estampado de traza funciona y el libro real nace limpio en la primera
     corrida diaria.
  3. **`--no-snapshots`.** 308 snapshots de historia que nadie va a peritar
     sólo agregan peso sobre la unidad de red.

Uso
---
    # ver qué haría, sin tocar nada ni salir a la red
    python scripts/recarga_historica.py --dry-run

    # el rango completo
    python scripts/recarga_historica.py

    # un tramo, para retomar o para probar
    python scripts/recarga_historica.py --desde 2025-01 --hasta 2025-03

Es reanudable: un mes cuyo `results_*.csv` ya cerró bien se saltea, de modo que
un corte a mitad de camino no obliga a empezar de nuevo.
"""

import argparse
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from scripts.run_daily_etl import leer_ultimo_resultado
from config.settings import ETL_MAX_LINEAS_FALLIDAS, PATHS
from typack_etl.extractor.utils.date_utils import (
    a_utc,
    fmt_api,
    inicio_mes_operativo,
    mes_operativo,
    month_name_es,
    now_planta,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# El mes más antiguo que hay en producción (`raw/2024/05`).
MES_MAS_ANTIGUO = (2024, 5)

# Una hora por mes: mismo orden de magnitud que la ventana diaria de 35 días.
TIMEOUT_POR_MES = 3600

SALIDA_OK = 0
SALIDA_FALLO = 1
SALIDA_DESTINO_SUCIO = 3


# ---------------------------------------------------------------------------
# Rango de meses
# ---------------------------------------------------------------------------

def parse_mes(texto: str) -> tuple[int, int]:
    """Convierte 'YYYY-MM' en (año, mes). Lanza ValueError si no cuadra."""
    fecha = datetime.strptime(str(texto).strip(), "%Y-%m")
    return fecha.year, fecha.month


def mes_en_curso() -> tuple[int, int]:
    """Mes operativo de hoy: el último que tiene sentido recargar."""
    return mes_operativo(now_planta())


def meses(desde: tuple[int, int], hasta: tuple[int, int]) -> list[tuple[int, int]]:
    """Todos los meses entre `desde` y `hasta`, ambos incluidos."""
    if desde > hasta:
        return []
    salida = []
    year, month = desde
    while (year, month) <= hasta:
        salida.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return salida


def rango_utc(year: int, month: int) -> tuple[str, str]:
    """Ventana del mes **operativo**, en el espacio UTC que espera la API.

    De corte a corte: el mes operativo de mayo va del 1 de mayo 12:00 UTC al 1
    de junio 12:00 UTC. Usar medianoche —como hacía el .bat viejo— dejaría
    afuera el turno de noche del día 1, que pertenece al mes anterior.
    """
    siguiente = (year + 1, 1) if month == 12 else (year, month + 1)
    inicio = a_utc(inicio_mes_operativo(year, month))
    fin = a_utc(inicio_mes_operativo(*siguiente))
    return fmt_api(inicio), fmt_api(fin)


# ---------------------------------------------------------------------------
# Estado de la recarga
# ---------------------------------------------------------------------------

def logs_del_mes(base_logs: Path, year: int, month: int) -> Path:
    """Cada mes lleva su propia bitácora: es lo que hace reanudable la recarga."""
    return Path(base_logs) / "recarga" / f"{year}-{month:02d}"


def mes_ya_recargado(logs_mes: Path, max_fallidas: int) -> bool:
    """True si ese mes ya cerró con una cantidad tolerable de líneas caídas."""
    resumen = leer_ultimo_resultado(logs_mes)
    return resumen is not None and resumen["fallidas"] <= max_fallidas


def particiones_ocupadas(output: Path, lista: list[tuple[int, int]]) -> list[Path]:
    """CSV que ya existen en las particiones que la recarga va a escribir.

    El destino sucio no es un detalle de higiene: es lo que hace que la traza
    salga mal (ver el encabezado del módulo).
    """
    output = Path(output)
    encontrados = []
    for year, month in lista:
        carpeta = output / str(year) / f"{month:02d}"
        if carpeta.exists():
            encontrados.extend(sorted(carpeta.glob("produccion_*.csv")))
    return encontrados


# ---------------------------------------------------------------------------
# Ejecución
# ---------------------------------------------------------------------------

def comando_del_mes(year, month, output: Path, logs_mes: Path, audit_dir: Path) -> list[str]:
    """Argumentos del extractor para un mes operativo."""
    inicio, fin = rango_utc(year, month)
    return [
        sys.executable, "-m", "src.typack_etl.extractor.pipelines.extractor_30m",
        "--start-date", inicio,
        "--end-date", fin,
        "--output", str(output),
        "--logs-dir", str(logs_mes),
        # Auditoría encendida (hace falta para estampar la traza) pero contra un
        # directorio que se descarta al terminar: el libro real nace limpio.
        "--audit-dir", str(audit_dir),
        "--no-snapshots",
        "--no-progress",
    ]


def recargar_mes(year, month, output, base_logs, audit_dir, max_fallidas,
                 timeout, logger) -> dict:
    """Baja un mes completo. Devuelve el resumen de cómo fue."""
    etiqueta = f"{year}-{month:02d} ({month_name_es(month)} {year})"
    logs_mes = logs_del_mes(base_logs, year, month)
    logs_mes.mkdir(parents=True, exist_ok=True)

    inicio, fin = rango_utc(year, month)
    logger.info(f"{etiqueta}: {inicio} -> {fin} (UTC)")

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    try:
        proceso = subprocess.run(
            comando_del_mes(year, month, output, logs_mes, audit_dir),
            capture_output=True, text=True, cwd=str(PROJECT_ROOT),
            timeout=timeout, env=env, encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        logger.error(f"{etiqueta}: superó el límite de {timeout / 60:.0f} min")
        return {"mes": etiqueta, "ok": False, "motivo": "timeout"}

    if proceso.returncode != 0:
        salida = (proceso.stdout or "") + (proceso.stderr or "")
        logger.error(f"{etiqueta}: el extractor salió con {proceso.returncode}\n{salida[-2000:]}")
        return {"mes": etiqueta, "ok": False, "motivo": f"exit {proceso.returncode}"}

    resumen = leer_ultimo_resultado(logs_mes)
    if resumen is None:
        logger.error(f"{etiqueta}: no dejó results_*.csv; no se puede verificar")
        return {"mes": etiqueta, "ok": False, "motivo": "sin bitácora"}

    for aviso in resumen["alertas"]:
        logger.warning(f"  {etiqueta}: {aviso}")

    ok = resumen["fallidas"] <= max_fallidas
    logger.info(
        f"{etiqueta}: {resumen['filas']} filas | líneas OK {resumen['ok']}/{resumen['total']}"
        + ("" if ok else f" | DEMASIADAS FALLAS (máx {max_fallidas})")
    )
    return {
        "mes": etiqueta,
        "ok": ok,
        "filas": resumen["filas"],
        "lineas_ok": resumen["ok"],
        "lineas_totales": resumen["total"],
        "lineas_fallidas": resumen["lineas_fallidas"],
        "motivo": "" if ok else "líneas fallidas",
    }


def setup_logging(base_logs: Path) -> logging.Logger:
    """Bitácora propia de la recarga, aparte de la del ETL diario."""
    directorio = Path(base_logs) / "recarga"
    directorio.mkdir(parents=True, exist_ok=True)
    destino = directorio / f"recarga_{datetime.now():%Y%m%d_%H%M%S}.log"

    logger = logging.getLogger("recarga_historica")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formato = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s",
                                datefmt="%Y-%m-%d %H:%M:%S")
    for handler in (logging.FileHandler(destino, encoding="utf-8"), logging.StreamHandler()):
        handler.setFormatter(formato)
        logger.addHandler(handler)
    return logger


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Recarga la base histórica con el esquema auditable.",
    )
    p.add_argument("--desde", help=f"Primer mes YYYY-MM (default: {MES_MAS_ANTIGUO[0]}-{MES_MAS_ANTIGUO[1]:02d})")
    p.add_argument("--hasta", help="Último mes YYYY-MM (default: el mes operativo en curso)")
    p.add_argument("--output", help="Directorio base de salida (default: PATH_RAW)")
    p.add_argument("--logs-dir", help="Directorio base de bitácoras (default: PATH_LOGS)")
    p.add_argument("--audit-dir", help="Libro desechable de la recarga (default: <logs>/recarga/_audit)")
    p.add_argument("--max-fallidas", type=int, default=ETL_MAX_LINEAS_FALLIDAS,
                   help=f"Líneas que pueden fallar por mes antes de abortar (default: {ETL_MAX_LINEAS_FALLIDAS})")
    p.add_argument("--timeout", type=int, default=TIMEOUT_POR_MES,
                   help=f"Segundos por mes (default: {TIMEOUT_POR_MES})")
    p.add_argument("--dry-run", action="store_true",
                   help="Muestra el plan y sale, sin llamar a la API")
    p.add_argument("--rehacer", action="store_true",
                   help="No saltea los meses que ya cerraron bien")
    p.add_argument("--permitir-destino-sucio", action="store_true",
                   help="Recarga aunque haya CSV previos. NO usar salvo que sepas por qué: "
                        "la traza sale mal (ver el encabezado del módulo)")
    p.add_argument("--continuar-con-errores", action="store_true",
                   help="Sigue con los meses siguientes en vez de abortar al primer fallo")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    desde = parse_mes(args.desde) if args.desde else MES_MAS_ANTIGUO
    hasta = parse_mes(args.hasta) if args.hasta else mes_en_curso()
    lista = meses(desde, hasta)
    if not lista:
        print(f"Rango vacío: {desde} > {hasta}")
        return SALIDA_FALLO

    output = Path(args.output) if args.output else Path(PATHS.get("raw", "data/raw"))
    base_logs = Path(args.logs_dir) if args.logs_dir else Path(PATHS.get("logs", "logs"))
    audit_dir = Path(args.audit_dir) if args.audit_dir else base_logs / "recarga" / "_audit"

    if args.dry_run:
        print(f"Recargaría {len(lista)} mes(es) en {output}")
        print(f"Libro desechable: {audit_dir}")
        for year, month in lista:
            inicio, fin = rango_utc(year, month)
            print(f"  {year}-{month:02d}  {inicio} -> {fin}  (UTC)")
        ocupadas = particiones_ocupadas(output, lista)
        if ocupadas:
            print(f"\nATENCIÓN: el destino tiene {len(ocupadas)} CSV previos. Hay que vaciarlo antes.")
        return SALIDA_OK

    ocupadas = particiones_ocupadas(output, lista)
    if ocupadas and not args.permitir_destino_sucio:
        print(
            f"El destino no está vacío: {len(ocupadas)} CSV en {output}.\n"
            "Recargar encima de la base vieja marcaría 'reasignada=1' en miles de filas\n"
            "por diferencias de formato, y eso llegaría al consolidado.\n"
            "Vaciá (o mové) las particiones y volvé a correr.\n"
            f"Primero: {ocupadas[0]}"
        )
        return SALIDA_DESTINO_SUCIO

    logger = setup_logging(base_logs)
    logger.info("=" * 60)
    logger.info(f"RECARGA HISTÓRICA: {len(lista)} mes(es), de {desde[0]}-{desde[1]:02d} a {hasta[0]}-{hasta[1]:02d}")
    logger.info(f"Salida: {output}")
    logger.info(f"Libro desechable: {audit_dir}")
    logger.info("=" * 60)

    arranque = datetime.now()
    resultados, salteados = [], []

    for year, month in lista:
        if not args.rehacer and mes_ya_recargado(logs_del_mes(base_logs, year, month), args.max_fallidas):
            logger.info(f"{year}-{month:02d}: ya recargado, se saltea")
            salteados.append(f"{year}-{month:02d}")
            continue

        resultado = recargar_mes(year, month, output, base_logs, audit_dir,
                                 args.max_fallidas, args.timeout, logger)
        resultados.append(resultado)

        if not resultado["ok"] and not args.continuar_con_errores:
            logger.error(
                f"Se aborta en {resultado['mes']} ({resultado['motivo']}). "
                "Corregí y volvé a correr: los meses ya cerrados se saltean solos."
            )
            break

    fallidos = [r for r in resultados if not r["ok"]]
    logger.info("=" * 60)
    logger.info(f"Meses recargados: {len(resultados) - len(fallidos)} | fallidos: {len(fallidos)} | salteados: {len(salteados)}")
    logger.info(f"Filas totales: {sum(r.get('filas', 0) for r in resultados)}")
    logger.info(f"Duración: {(datetime.now() - arranque).total_seconds() / 60:.1f} min")
    for r in fallidos:
        logger.error(f"  FALLÓ {r['mes']}: {r['motivo']}")
    logger.info("=" * 60)

    if fallidos:
        return SALIDA_FALLO

    logger.info(
        "Recarga completa. Pasos siguientes: reprocesar con el transformer SIN "
        "--incremental, y descartar el libro desechable para que el real nazca limpio."
    )
    return SALIDA_OK


if __name__ == "__main__":
    sys.exit(main())
