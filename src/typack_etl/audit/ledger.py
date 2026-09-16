"""
Persistencia de la auditoría: libro de cambios, cobertura diaria y run_id.

El libro es append-only: nunca se reescribe una fila existente. Cada corrida
agrega lo que detectó, de modo que la historia de un segmento se lee siguiendo
su `record_key` en orden cronológico.
"""

from __future__ import annotations

import csv
import io as _io
import json
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence, Set, Tuple

from typack_etl.audit.cdc import DESAPARECIDO, REAPARECIDO, Cambio
from typack_etl.extractor.functions.io import atomic_open, read_csv_rows
from typack_etl.extractor.utils.date_utils import (
    dia_operativo,
    inicio_dia_operativo,
    minutos_dia_operativo,
    parse_api_datetime,
)

LEDGER_COLUMNS: Tuple[str, ...] = (
    "record_key",
    "devid",
    "linea",
    "maquina",
    "start",
    "end",
    "duration",
    "detectado_en",
    "dias_desde_evento",
    "run_id_anterior",
    "run_id_actual",
    "tipo_cambio",
    "campo",
    "valor_anterior",
    "valor_nuevo",
    "usuario_anterior",
    "usuario_nuevo",
)

COBERTURA_COLUMNS: Tuple[str, ...] = (
    "linea",
    "devid",
    "fecha",
    "minutos",
    "pct_cobertura",
    "run_id",
    "actualizado_en",
)

LEDGER_FILENAME = "detenciones_audit.csv"
COBERTURA_FILENAME = "cobertura_diaria.csv"
CORRIDAS_FILENAME = "ultimas_corridas.json"

# Tolerancia para no alertar por diferencias de redondeo entre corridas.
TOLERANCIA_MINUTOS = 1.0


# ---------------------------------------------------------------------------
# Libro de cambios
# ---------------------------------------------------------------------------

def append_cambios(cambios: Sequence[Cambio], audit_dir: Path) -> Path:
    """Agrega cambios al final del libro, creando la cabecera si hace falta.

    Las filas se serializan a memoria y se vuelcan en una sola escritura, para
    que una caída no deje media fila a mitad del archivo.
    """
    path = Path(audit_dir) / LEDGER_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cambios:
        return path

    buffer = _io.StringIO()
    writer = csv.DictWriter(
        buffer, fieldnames=list(LEDGER_COLUMNS), extrasaction="ignore", delimiter=";"
    )
    if not path.exists():
        writer.writeheader()
    for cambio in cambios:
        writer.writerow({k: v for k, v in asdict(cambio).items() if k in LEDGER_COLUMNS})

    with path.open("a", encoding="utf-8-sig", newline="") as f:
        f.write(buffer.getvalue())
    return path


def claves_ausentes(audit_dir: Path) -> Set[str]:
    """record_keys cuyo último evento en el libro fue DESAPARECIDO.

    Permite distinguir un REAPARECIDO (una clave que el libro ya dio por
    perdida y volvió) de un alta normal.
    """
    path = Path(audit_dir) / LEDGER_FILENAME
    estado: Dict[str, str] = {}
    for row in read_ledger(path):
        tipo = row.get("tipo_cambio")
        if tipo in (DESAPARECIDO, REAPARECIDO):
            estado[row.get("record_key", "")] = tipo
    return {k for k, tipo in estado.items() if tipo == DESAPARECIDO}


def read_ledger(path: Path) -> List[Dict[str, str]]:
    """Lee el libro completo. Archivo inexistente -> []."""
    path = Path(path)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return [dict(row) for row in csv.DictReader(f, delimiter=";")]


def reasignaciones_recientes(audit_dir: Path, dias: int = 30) -> List[Dict[str, str]]:
    """Reasignaciones detectadas dentro de los `dias` posteriores al evento.

    Es la vista que responde la pregunta del negocio: qué detenciones cambiaron
    de asignación dentro de la ventana.
    """
    from typack_etl.audit.cdc import REASIGNACION

    filas = []
    for row in read_ledger(Path(audit_dir) / LEDGER_FILENAME):
        if row.get("tipo_cambio") != REASIGNACION:
            continue
        try:
            if int(row.get("dias_desde_evento", -1)) <= dias:
                filas.append(row)
        except ValueError:
            continue
    return filas


# ---------------------------------------------------------------------------
# Cobertura diaria
# ---------------------------------------------------------------------------

def _minutos(value) -> float:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


def _reparto_por_dia(row: Mapping) -> Dict[str, float]:
    """Reparte la duración de un segmento entre los días operativos que abarca.

    Hay detenciones que duran días: en producción se observó una de 46 h. Si se
    le atribuyeran todos sus minutos al día en que empezó, ese día superaría el
    100 % de cobertura y los siguientes aparecerían vacíos, que es justo lo que
    la métrica busca detectar. El reparto es proporcional al tiempo que el
    segmento pasa dentro de cada día, y conserva el total.
    """
    minutos = _minutos(row.get("duration"))
    inicio = parse_api_datetime(row.get("start"))
    if inicio is None:
        return {}

    fin = parse_api_datetime(row.get("end"))
    dia_inicial = dia_operativo(inicio)
    if fin is None or fin <= inicio:
        # Sin fin utilizable o de duración cero: todo al día en que empezó.
        return {dia_inicial.strftime("%Y-%m-%d"): minutos}

    total = (fin - inicio).total_seconds()
    reparto: Dict[str, float] = {}
    dia = dia_inicial
    while inicio_dia_operativo(dia) < fin:
        # Cada borde sale del calendario local, no de sumar 24 h: en un cambio
        # de hora el día operativo dura 23 o 25, y sumar un día fijo dejaría un
        # hueco o un solape entre días consecutivos.
        arranca = inicio_dia_operativo(dia)
        termina = inicio_dia_operativo(dia + timedelta(days=1))
        solape = (min(fin, termina) - max(inicio, arranca)).total_seconds()
        if solape > 0:
            reparto[dia.strftime("%Y-%m-%d")] = minutos * solape / total
        dia += timedelta(days=1)
    return reparto or {dia_inicial.strftime("%Y-%m-%d"): minutos}


def cobertura_por_dia(
    rows: Iterable[Mapping],
    ventana_inicio: datetime,
    ventana_fin: datetime,
) -> Dict[str, float]:
    """Minutos reportados por día operativo, sólo para los días completos.

    El día operativo va de corte a corte (24 h alineadas al cambio de turno),
    no de medianoche a medianoche, de modo que un turno de noche cuenta entero
    en el día en que empezó.

    Un día parcial en el borde de la ventana siempre parecería incompleto, así
    que se excluye: alertaría en cada corrida.
    """
    acumulado: Dict[str, float] = defaultdict(float)
    for row in rows:
        for fecha, minutos in _reparto_por_dia(row).items():
            acumulado[fecha] += minutos

    completos = {}
    for fecha, minutos in acumulado.items():
        dia = datetime.strptime(fecha, "%Y-%m-%d").date()
        inicio = inicio_dia_operativo(dia)
        if inicio >= ventana_inicio and inicio_dia_operativo(dia + timedelta(days=1)) <= ventana_fin:
            completos[fecha] = minutos
    return completos


def registrar_cobertura(
    linea: str,
    devid,
    cobertura: Mapping[str, float],
    audit_dir: Path,
    run_id: str,
    detectado_en: datetime,
) -> List[Dict[str, object]]:
    """Guarda la cobertura del día y devuelve las caídas respecto de la corrida previa.

    Una caída significa que la API dejó de reportar tiempo que antes reportaba
    para un día ya cerrado: es el patrón de la pérdida de 44 h por línea que se
    observó entre las extracciones de marzo.
    """
    path = Path(audit_dir) / COBERTURA_FILENAME
    previas = {(r["linea"], r["fecha"]): r for r in read_csv_rows(path)}

    caidas = []
    for fecha, minutos in cobertura.items():
        dia = datetime.strptime(fecha, "%Y-%m-%d").date()
        esperados = minutos_dia_operativo(dia)
        anterior = previas.get((linea, fecha))
        if anterior is not None:
            minutos_antes = _minutos(anterior.get("minutos"))
            # Un día que reporta su jornada completa nunca es una caída, por
            # más que la corrida anterior hubiera anotado más: lo que sobraba
            # era doble conteo. Pasa una vez por cada cambio de hora.
            completo = minutos >= esperados - TOLERANCIA_MINUTOS
            if not completo and minutos < minutos_antes - TOLERANCIA_MINUTOS:
                caidas.append({
                    "linea": linea,
                    "fecha": fecha,
                    "minutos_antes": round(minutos_antes, 1),
                    "minutos_ahora": round(minutos, 1),
                    "delta": round(minutos - minutos_antes, 1),
                })
        previas[(linea, fecha)] = {
            "linea": linea,
            "devid": devid,
            "fecha": fecha,
            "minutos": round(minutos, 2),
            # El denominador es lo que dura ese día operativo, no 1440: en los
            # dos cambios de hora del año la noche tiene 23 o 25 horas.
            "pct_cobertura": round(100 * minutos / esperados, 2),
            "run_id": run_id,
            "actualizado_en": detectado_en.strftime("%Y-%m-%d %H:%M:%S"),
        }

    filas = sorted(previas.values(), key=lambda r: (str(r["linea"]), str(r["fecha"])))
    with atomic_open(path) as f:
        writer = csv.DictWriter(
            f, fieldnames=list(COBERTURA_COLUMNS), extrasaction="ignore", delimiter=";"
        )
        writer.writeheader()
        writer.writerows(filas)
    return caidas


# ---------------------------------------------------------------------------
# run_id de la corrida anterior
# ---------------------------------------------------------------------------

def leer_ultimas_corridas(audit_dir: Path) -> Dict[str, str]:
    """run_id de la última corrida que escribió cada línea."""
    path = Path(audit_dir) / CORRIDAS_FILENAME
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def guardar_ultimas_corridas(corridas: Mapping[str, str], audit_dir: Path) -> Path:
    path = Path(audit_dir) / CORRIDAS_FILENAME
    with atomic_open(path, encoding="utf-8", newline="\n") as f:
        json.dump(dict(corridas), f, ensure_ascii=False, indent=2, sort_keys=True)
    return path
