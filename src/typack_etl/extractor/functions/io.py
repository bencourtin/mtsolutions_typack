"""
Utilidades compartidas de E/S para los pipelines del extractor.

Este módulo centraliza lo que antes estaba duplicado (y ya divergido) entre
los pipelines del extractor: normalización de la respuesta de la API, formato
de valores, escritura de CSV y bitácoras de corrida.

Dos garantías que aportan estas funciones frente a las copias anteriores:

  1. Esquema canónico fijo (`SABANA_COLUMNS`). La API omite las claves nulas,
     de modo que armar la cabecera con la unión de claves presentes producía
     archivos de la misma serie con distinto número y orden de columnas
     (los CSV con `order` y sin `order` conviven hoy en data/). Ahora la
     cabecera es siempre la misma y las columnas ausentes se escriben vacías.

  2. Escritura atómica. Todo se escribe primero en un `.tmp` junto al destino
     y recién al cerrar se hace `os.replace()`. Si la corrida se cae a mitad
     de la escritura, el archivo anterior queda intacto en vez de quedar
     truncado (relevante porque el destino es una unidad de red).
"""

from __future__ import annotations

import csv
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Generator, Iterable, Iterator, List, Mapping, Sequence, Tuple
from uuid import uuid4

from typack_etl.extractor.utils.date_utils import mes_operativo, now_planta, parse_api_datetime

# Esquema canónico del reporte "sabana".
# Orden: identidad temporal -> contexto de línea -> orden de fabricación ->
# producción -> códigos de detención -> texto libre.
SABANA_COLUMNS: tuple[str, ...] = (
    "start",
    "end",
    "duration",
    "device",
    "line",
    "plant",
    "turn",
    "order",
    "sku",
    "product",
    "format",
    "production",
    "code_description",
    "code_u",
    "code_f",
    "code_t",
    "code_l",
    "code_m",
    "code_n",
    "comments",
    "user",
)

# Columnas propias, no de la API: permiten unir cada fila con el libro de
# auditoría y medir el retraso de asignación sin salir del archivo de datos.
# Ver `typack_etl.audit.traza`.
COLUMNAS_TRAZA: tuple[str, ...] = (
    "record_key",
    "run_id_alta",
    "run_id_version",
    "reasignada",
    "fecha_reasignacion",
)

CSV_DELIMITER = ";"
CSV_ENCODING = "utf-8-sig"

PLAN_COLUMNS = ("line", "devid", "start", "end", "output_path")
RESULTS_COLUMNS = (
    "line", "devid", "start", "end", "rows",
    "success", "estado", "cobertura_pct", "error", "detalle",
)


# ---------------------------------------------------------------------------
# Escritura atómica
# ---------------------------------------------------------------------------

@contextmanager
def atomic_open(path: Path, encoding: str = CSV_ENCODING, newline: str = "") -> Generator:
    """Abre `path` para escritura atómica.

    Escribe en `<path>.tmp` (mismo directorio, por lo que `os.replace` es
    atómico) y sólo reemplaza el destino si el bloque termina sin excepción.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with tmp.open("w", encoding=encoding, newline=newline) as f:
            yield f
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------------------
# Normalización de la respuesta
# ---------------------------------------------------------------------------

def infer_rows(data) -> List[Mapping]:
    """Normaliza la respuesta de la API a una lista de dicts (una por fila).

    Acepta:
      - lista de dicts -> se retorna tal cual
      - dict con clave 'data', 'results', 'items' o 'rows' que contenga una
        lista -> retorna esa lista
      - dict simple -> lo envuelve en una lista
      - cualquier otro -> lista vacía
    """
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("data", "results", "items", "rows"):
            if key in data and isinstance(data[key], list):
                return [x for x in data[key] if isinstance(x, dict)]
        return [data]
    return []


def fieldnames_for(rows: Sequence[Mapping]) -> List[str]:
    """Cabecera canónica, columnas de traza, y cualquier columna nueva de la API.

    El esquema fijo va siempre primero y completo; las columnas desconocidas
    se anexan al final en orden de aparición para no perder datos en silencio
    si el proveedor agrega campos.
    """
    base = list(SABANA_COLUMNS) + list(COLUMNAS_TRAZA)
    seen = set(base)
    extra: List[str] = []
    for row in rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                extra.append(key)
    return base + extra


def format_value(value):
    """Convierte valores numéricos a cadena con coma decimal.

    Reglas:
      - float -> reemplaza punto por coma
      - int -> str normal
      - str que representa un decimal (ej: "12.34") -> reemplaza punto por coma
      - None -> cadena vacía (columna ausente en la respuesta)
      - otros tipos -> se devuelve tal cual
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return ("%s" % value).replace(".", ",")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        if "." in value and value.replace("-", "").replace(".", "", 1).isdigit():
            return value.replace(".", ",")
        return value
    return value


def safe_filename(text: str) -> str:
    """Deja sólo caracteres válidos para un nombre de archivo."""
    return "".join(c if c.isalnum() or c in ("-", "_", ".") else "_" for c in str(text))


# ---------------------------------------------------------------------------
# CSV de datos
# ---------------------------------------------------------------------------

def write_csv(rows: Sequence[Mapping], output_path: Path) -> int:
    """Escribe las filas en `output_path` con el esquema canónico.

    A diferencia de la versión anterior, un resultado sin filas ya no genera un
    archivo vacío sino uno con la cabecera canónica: pandas puede leerlo como
    DataFrame vacío en lugar de fallar con EmptyDataError.

    Retorna la cantidad de filas escritas.
    """
    output_path = Path(output_path)
    fieldnames = fieldnames_for(rows)
    with atomic_open(output_path) as f:
        writer = csv.DictWriter(
            f, fieldnames=fieldnames, extrasaction="ignore", delimiter=CSV_DELIMITER
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({k: format_value(row.get(k)) for k in fieldnames})
    return len(rows)


def read_csv_rows(path: Path) -> List[Dict[str, str]]:
    """Lee un CSV escrito por `write_csv`. Archivo inexistente o vacío -> []."""
    path = Path(path)
    if not path.exists():
        return []
    with path.open("r", encoding=CSV_ENCODING, newline="") as f:
        return [dict(row) for row in csv.DictReader(f, delimiter=CSV_DELIMITER)]


# ---------------------------------------------------------------------------
# Particionado por mes operativo del registro
# ---------------------------------------------------------------------------

def month_partitions(window_start: datetime, window_end: datetime) -> List[Tuple[int, int]]:
    """Meses operativos que toca una ventana, en orden cronológico.

    El mes operativo va del día 1 en el corte al día 1 del mes siguiente en el
    corte, no de medianoche a medianoche.

    Se calcula desde la ventana y no desde los datos: si una línea no produjo
    nada en un mes de la ventana, esa partición igual debe visitarse para que
    el reemplazo por rango borre lo que la API ya no reporta.

    El fin se trata como exclusivo: una ventana que termina exactamente en el
    corte del día 1 no arrastra el mes siguiente entero por un solo instante.
    Las particiones que la respuesta traiga de más se agregan al procesarla.
    """
    partitions: List[Tuple[int, int]] = []
    year, month = mes_operativo(window_start)
    ultimo = mes_operativo(max(window_start, window_end - timedelta(seconds=1)))
    while (year, month) <= ultimo:
        partitions.append((year, month))
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return partitions


def partition_by_month(
    rows: Sequence[Mapping],
) -> Tuple[Dict[Tuple[int, int], List[Mapping]], List[Mapping]]:
    """Agrupa filas por mes operativo de su `start`.

    Un registro del día 1 a las 06:00 cae antes del corte, así que pertenece al
    mes anterior: es el turno que arrancó el último día del mes.

    Retorna (particiones, sin_fecha). Las filas cuyo `start` no se puede
    interpretar se devuelven aparte en vez de asignarse a un mes arbitrario.
    """
    partitions: Dict[Tuple[int, int], List[Mapping]] = {}
    undated: List[Mapping] = []
    for row in rows:
        started = parse_api_datetime(row.get("start"))
        if started is None:
            undated.append(row)
            continue
        partitions.setdefault(mes_operativo(started), []).append(row)
    return partitions, undated


def identidad(row: Mapping) -> Tuple[str, str]:
    """Identidad de un segmento dentro de una partición: (start, end) normalizados.

    Se normaliza con `format_value` porque un lado viene de la API (floats,
    ints) y el otro de un CSV ya escrito (texto).
    """
    return (
        str(format_value(row.get("start"))).strip(),
        str(format_value(row.get("end"))).strip(),
    )


def solapa_ventana(row: Mapping, inicio: datetime, fin: datetime) -> bool:
    """True si el segmento cae, aunque sea en parte, dentro de la ventana.

    El criterio es solape de intervalos y no "empieza dentro", porque la API
    devuelve también los segmentos que arrancan antes del inicio de la ventana
    y la cruzan (los que pasan la medianoche). Si se los tratara como fuera de
    la ventana, la copia vieja se conservaría y la nueva se agregaría al lado:
    cada corrida duplicaría el borde de la ventana.

    Un segmento que termina exactamente donde empieza la ventana queda fuera:
    está íntegramente antes y la API no lo reporta.
    """
    start = parse_api_datetime(row.get("start"))
    if start is None or start > fin:
        return False
    end = parse_api_datetime(row.get("end"))
    if end is None or end == start:
        # Sin fin utilizable o de duración cero: decide el inicio.
        return start >= inicio
    return end > inicio


def write_window_csv(
    rows: Sequence[Mapping],
    output_path: Path,
    window_start: datetime,
    window_end: datetime,
    existing_rows: Sequence[Mapping] | None = None,
) -> Dict[str, int]:
    """Escribe una partición reemplazando sólo el tramo que cubre la ventana.

    Una ventana móvil de 35 días toca un mes de forma parcial: al re-extraer en
    abril, la partición de marzo sólo se refresca desde el día que cubre la
    ventana. Las filas anteriores del mismo archivo se conservan tal cual, de
    modo que la extracción diaria nunca destruye el histórico del mes.

    Las filas dentro de la ventana se descartan y se sustituyen por `rows`: si
    la API dejó de reportar un segmento, el archivo deja de tenerlo.
    """
    kept: List[Mapping] = []
    replaced = 0
    undated = 0
    previas = read_csv_rows(output_path) if existing_rows is None else existing_rows
    claves_nuevas = {identidad(r) for r in rows}
    for row in previas:
        if identidad(row) in claves_nuevas:
            # La API acaba de reportar este mismo segmento: gana la versión nueva.
            replaced += 1
        elif parse_api_datetime(row.get("start")) is None:
            kept.append(row)  # no se puede clasificar: se conserva
            undated += 1
        elif solapa_ventana(row, window_start, window_end):
            replaced += 1  # estaba en la ventana y la API ya no lo reporta
        else:
            kept.append(row)

    merged = kept + list(rows)
    merged.sort(key=_chronological_key)
    write_csv(merged, output_path)
    return {
        "total": len(merged),
        "nuevas": len(rows),
        "conservadas": len(kept),
        "reemplazadas": replaced,
        "sin_fecha": undated,
    }


def _chronological_key(row: Mapping) -> Tuple[datetime, str]:
    started = parse_api_datetime(row.get("start")) or datetime.min
    return started, str(row.get("end") or "")


# ---------------------------------------------------------------------------
# Bitácoras de corrida
# ---------------------------------------------------------------------------

def run_stamp() -> str:
    """Marca de tiempo de la corrida, compartida por plan y resultados."""
    return now_planta().strftime("%Y%m%d_%H%M%S")


def nuevo_run_id(stamp: str | None = None) -> str:
    """Identificador único de la corrida, que acompaña a cada artefacto.

    El sufijo aleatorio evita que dos corridas lanzadas en el mismo segundo
    compartan identificador.
    """
    return f"{stamp or run_stamp()}_{uuid4().hex[:8]}"


def _write_log(items: Sequence[Mapping], path: Path, columns: Sequence[str]) -> Path:
    with atomic_open(path, encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=list(columns), extrasaction="ignore", delimiter=CSV_DELIMITER
        )
        writer.writeheader()
        for item in items:
            writer.writerow({k: item.get(k, "") for k in columns})
    return path


def write_plan_log(items: Sequence[Mapping], logs_dir: Path, stamp: str) -> Path:
    """Escribe plan_<stamp>.csv: qué se va a descargar y dónde se va a guardar."""
    return _write_log(items, Path(logs_dir) / f"plan_{stamp}.csv", PLAN_COLUMNS)


def write_results_log(items: Sequence[Mapping], logs_dir: Path, stamp: str) -> Path:
    """Escribe results_<stamp>.csv: qué se descargó efectivamente.

    Comparte `stamp` con el plan de la misma corrida, de modo que ambos
    archivos se puedan aparear (antes cada uno tomaba su propia hora y los
    pares quedaban desalineados).
    """
    rows = [dict(it, success=int(bool(it.get("success")))) for it in items]
    return _write_log(rows, Path(logs_dir) / f"results_{stamp}.csv", RESULTS_COLUMNS)


# ---------------------------------------------------------------------------
# Presentación
# ---------------------------------------------------------------------------

def _caracteres_barra() -> Tuple[str, str]:
    """Caracteres de la barra que la salida actual sea capaz de imprimir.

    Redirigir la salida en una consola Windows deja stdout en cp1252, donde
    '█' no existe: sin este respaldo la barra aborta la corrida entera con
    UnicodeEncodeError.
    """
    try:
        "█·".encode(getattr(sys.stdout, "encoding", None) or "utf-8")
        return "█", "·"
    except (UnicodeEncodeError, LookupError):
        return "#", "-"


def rotar_logs(logs_dir: Path, dias: int) -> int:
    """Borra los plan_/results_ más viejos que `dias`.

    Se generan dos por corrida y nunca se limpiaban: en producción ya había
    474 archivos acumulados. Retorna cuántos se eliminaron.

    Sólo mira el directorio que recibe, sin recorrer subcarpetas. El extractor
    mensual dejaba las suyas en <logs>/<año>/<mes>, y borrarlas de rebote sería
    un efecto colateral que nadie pidió: esas se limpian a mano.

    El libro de auditoría NO se toca: ese es justamente el histórico que hay
    que conservar.
    """
    logs_dir = Path(logs_dir)
    if dias <= 0 or not logs_dir.exists():
        return 0

    limite = now_planta() - timedelta(days=dias)
    borrados = 0
    for patron in ("plan_*.csv", "results_*.csv"):
        for archivo in logs_dir.glob(patron):
            marca = archivo.stem.split("_", 1)[-1]
            try:
                cuando = datetime.strptime(marca, "%Y%m%d_%H%M%S")
            except ValueError:
                continue  # nombre inesperado: no se toca
            if cuando < limite:
                try:
                    archivo.unlink()
                    borrados += 1
                except OSError:
                    pass
    return borrados


def progress(iterable: Iterable, total: int, enabled: bool = True) -> Iterator:
    """Barra de progreso simple, sin dependencias externas."""
    if not enabled or not total:
        yield from iterable
        return
    bar_len = 30
    lleno, vacio = _caracteres_barra()
    for i, item in enumerate(iterable, 1):
        pct = i / total
        filled = int(bar_len * pct)
        bar = lleno * filled + vacio * (bar_len - filled)
        print(f"\r[{bar}] {i}/{total} ({pct*100:5.1f}%)", end="", flush=True)
        yield item
    print()
