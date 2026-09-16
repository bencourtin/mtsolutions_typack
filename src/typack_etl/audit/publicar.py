"""
Publicación de la auditoría junto al consolidado.

Por qué existe
--------------
El libro (`detenciones_audit.csv`) vive en `PATH_AUDIT` y hasta ahora no lo leía
nadie: Power BI sólo mira `PATH_CONSOLIDATED`. El consolidado ya trae la clave
de unión y el flag (`Record_Key`, `Reasignada`), de modo que puede responder
*si* a una detención le cambiaron el código, pero no *de qué código a cuál, ni
cuándo*. Este módulo cierra esa distancia publicando el detalle en la carpeta
que el BI ya lee.

Qué se publica, y qué no
------------------------
El libro entero, sin ventana temporal. Contra-intuitivo para un archivo
append-only, pero lo sostiene el volumen: filtrado a reasignaciones y colapsado
por evento, el libro real de 174 filas queda en 14. Una ventana sólo agregaría
un parámetro que discutir y huecos en el join.

No se publican `DESAPARECIDO`, `RECORTE_SEGMENTO` ni `OTRO_CAMBIO`: son señales
de operación, no preguntas de negocio. Siguen en el libro, que es su lugar.

Dos transformaciones que el grano del libro exige
-------------------------------------------------
1. **Colapso por evento.** El libro escribe una fila por *campo* modificado, y
   una reclasificación mueve `code_t` y `code_description` a la vez: dos filas
   que describen el mismo hecho, porque la descripción es la etiqueta del
   código. Acá la descripción viaja plegada en `Desc_Anterior`/`Desc_Nuevo`
   sobre la fila del código.

2. **Deduplicación.** `--output` y `--audit-dir` son flags independientes del
   extractor, así que una corrida que escriba los datos en otro lado vuelve a
   comparar contra el mismo archivo viejo y registra la misma reasignación otra
   vez. En el libro real eso ocurrió 6 veces sobre los mismos 14 segmentos. Se
   deduplica por `(record_key, campo, valor_anterior, valor_nuevo)`, que no
   pierde reclasificaciones genuinas: si a un segmento le cambian el código dos
   veces de verdad, el `valor_anterior` de la segunda es el `valor_nuevo` de la
   primera y la tupla difiere.
"""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path
from statistics import median
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

from typack_etl.audit.cdc import REASIGNACION
from typack_etl.audit.keys import ASSIGNMENT_FIELDS
from typack_etl.audit.ledger import COBERTURA_FILENAME, LEDGER_FILENAME, read_ledger
from typack_etl.extractor.functions.io import CSV_DELIMITER, atomic_open, read_csv_rows

# La descripción es la etiqueta del código, no un cambio aparte: se pliega.
CAMPO_DESCRIPCION = "code_description"
CAMPOS_CODIGO: Tuple[str, ...] = tuple(c for c in ASSIGNMENT_FIELDS if c != CAMPO_DESCRIPCION)

# Segmentos de duración cero en el mismo instante que el CDC no pudo aparear
# uno a uno. El cambio es real pero no se atribuye a un campo concreto.
CAMPO_GRUPO = "grupo_instante_unico"

REASIGNACIONES_FILENAME = "mt_typ_reasignaciones.csv"
COBERTURA_PUBLICADA_FILENAME = "mt_typ_cobertura.csv"

# Nombres en la convención del consolidado, para que el modelo del BI no tenga
# que renombrar nada al relacionar las dos tablas.
REASIGNACIONES_COLUMNS: Tuple[str, ...] = (
    "Record_Key",
    "Linea",
    "Maquina",
    "Fecha_Hora_Inicio",
    "Fecha_Hora_Termino",
    "Duracion_min",
    "Detectado_En",
    "Dias_Desde_Evento",
    "Campo",
    "Cod_Anterior",
    "Cod_Nuevo",
    "Desc_Anterior",
    "Desc_Nuevo",
    "Usuario_Anterior",
    "Usuario_Nuevo",
    "Run_Id",
)

COBERTURA_PUBLICADA_COLUMNS: Tuple[str, ...] = (
    "Linea",
    "Devid",
    "Fecha",
    "Minutos",
    "Pct_Cobertura",
    "Run_Id",
    "Actualizado_En",
)

# Identidad de una reasignación, a efectos de deduplicar re-detecciones.
_IDENTIDAD = ("Record_Key", "Campo", "Cod_Anterior", "Cod_Nuevo", "Desc_Anterior", "Desc_Nuevo")


# ---------------------------------------------------------------------------
# Tabla de reasignaciones
# ---------------------------------------------------------------------------

def _fila(origen: Mapping, campo: str, anterior: str, nuevo: str,
          desc_anterior: str, desc_nuevo: str) -> Dict[str, str]:
    """Arma una fila publicada a partir de una fila del libro."""
    return {
        "Record_Key": origen.get("record_key", ""),
        "Linea": origen.get("linea", ""),
        "Maquina": origen.get("maquina", ""),
        "Fecha_Hora_Inicio": origen.get("start", ""),
        "Fecha_Hora_Termino": origen.get("end", ""),
        "Duracion_min": origen.get("duration", ""),
        "Detectado_En": origen.get("detectado_en", ""),
        "Dias_Desde_Evento": origen.get("dias_desde_evento", ""),
        "Campo": campo,
        "Cod_Anterior": anterior,
        "Cod_Nuevo": nuevo,
        "Desc_Anterior": desc_anterior,
        "Desc_Nuevo": desc_nuevo,
        "Usuario_Anterior": origen.get("usuario_anterior", ""),
        "Usuario_Nuevo": origen.get("usuario_nuevo", ""),
        "Run_Id": origen.get("run_id_actual", ""),
    }


def eventos(libro: Iterable[Mapping]) -> List[Dict[str, str]]:
    """Colapsa las filas del libro en una fila por código reasignado.

    Agrupa por `(record_key, detectado_en)` —el evento— y dentro de cada grupo
    pliega la descripción sobre las filas de código. Los campos de contexto
    (`comments`, `user`, `production`…) que hayan cambiado en el mismo evento
    quedan fuera: son ediciones colaterales, no la reclasificación.
    """
    grupos: Dict[Tuple[str, str], List[Mapping]] = {}
    for row in libro:
        if row.get("tipo_cambio") != REASIGNACION:
            continue
        clave = (row.get("record_key", ""), row.get("detectado_en", ""))
        grupos.setdefault(clave, []).append(row)

    filas: List[Dict[str, str]] = []
    for grupo in grupos.values():
        por_campo = {r.get("campo", ""): r for r in grupo}
        desc = por_campo.get(CAMPO_DESCRIPCION)
        desc_anterior = desc.get("valor_anterior", "") if desc else ""
        desc_nuevo = desc.get("valor_nuevo", "") if desc else ""

        codigos = [(c, por_campo[c]) for c in CAMPOS_CODIGO if c in por_campo]
        if codigos:
            for campo, row in codigos:
                filas.append(_fila(row, campo, row.get("valor_anterior", ""),
                                   row.get("valor_nuevo", ""), desc_anterior, desc_nuevo))
        elif desc is not None:
            # Cambió la etiqueta sin que cambiara ningún código.
            filas.append(_fila(desc, CAMPO_DESCRIPCION, "", "", desc_anterior, desc_nuevo))
        elif CAMPO_GRUPO in por_campo:
            row = por_campo[CAMPO_GRUPO]
            filas.append(_fila(row, CAMPO_GRUPO, "", "",
                               row.get("valor_anterior", ""), row.get("valor_nuevo", "")))
    return filas


def deduplicar(filas: Sequence[Mapping]) -> List[Dict[str, str]]:
    """Descarta re-detecciones del mismo cambio, conservando la más antigua.

    La primera detección es la que responde la pregunta del negocio: cuánto
    tardó la planta en reasignar. Las repeticiones posteriores no aportan.
    """
    ordenadas = sorted(filas, key=lambda r: str(r.get("Detectado_En", "")))
    vistas = set()
    unicas = []
    for fila in ordenadas:
        identidad = tuple(str(fila.get(c, "")) for c in _IDENTIDAD)
        if identidad in vistas:
            continue
        vistas.add(identidad)
        unicas.append(dict(fila))
    return unicas


def _escribir(filas: Sequence[Mapping], path: Path, columnas: Sequence[str]) -> Path:
    """Vuelca las filas con el formato del consolidado, de forma atómica."""
    with atomic_open(path) as f:
        writer = csv.DictWriter(
            f, fieldnames=list(columnas), extrasaction="ignore", delimiter=CSV_DELIMITER
        )
        writer.writeheader()
        writer.writerows(filas)
    return path


def reasignaciones(audit_dir: Path) -> List[Dict[str, str]]:
    """Tabla de hechos lista para publicar: una fila por código reasignado."""
    libro = read_ledger(Path(audit_dir) / LEDGER_FILENAME)
    filas = deduplicar(eventos(libro))
    return sorted(filas, key=lambda r: (str(r["Fecha_Hora_Inicio"]),
                                        str(r["Record_Key"]), str(r["Campo"])))


# ---------------------------------------------------------------------------
# Resumen para el reporte semanal
# ---------------------------------------------------------------------------

def resumen(audit_dir: Path, desde: str, hasta: str) -> Dict[str, object]:
    """Reasignaciones **detectadas** entre dos fechas (`YYYY-MM-DD`, inclusive).

    Acá sí hay ventana, y va por `detectado_en`, no por `dias_desde_evento`: la
    pregunta del reporte es "qué cambió esta semana", distinta de la que
    responde el archivo publicado ("todo el historial"). Nótese que
    `ledger.reasignaciones_recientes` filtra por lo segundo, así que no sirve
    para esto: sobre el libro real, cuyos cambios se detectaron 148 días
    después del evento, devuelve cero filas.

    Cuenta eventos ya deduplicados, no filas del libro: seis re-detecciones de
    la misma reasignación son una, y una reclasificación que movió `code_t` y
    `code_description` también.
    """
    filas = [
        f for f in reasignaciones(audit_dir)
        if desde <= str(f.get("Detectado_En", ""))[:10] <= hasta
    ]

    retrasos = []
    for fila in filas:
        try:
            retrasos.append(int(fila["Dias_Desde_Evento"]))
        except (KeyError, TypeError, ValueError):
            continue

    por_linea = Counter(str(f.get("Linea", "")) for f in filas)
    return {
        "total": len(filas),
        "por_linea": dict(por_linea.most_common()),
        "retraso_mediano": round(median(retrasos), 1) if retrasos else None,
        "retraso_max": max(retrasos) if retrasos else None,
    }


# ---------------------------------------------------------------------------
# Cobertura
# ---------------------------------------------------------------------------

def cobertura(audit_dir: Path) -> List[Dict[str, str]]:
    """Cobertura diaria con los nombres de columna del consolidado."""
    origen = read_csv_rows(Path(audit_dir) / COBERTURA_FILENAME)
    filas = [
        {
            "Linea": r.get("linea", ""),
            "Devid": r.get("devid", ""),
            "Fecha": r.get("fecha", ""),
            "Minutos": r.get("minutos", ""),
            "Pct_Cobertura": r.get("pct_cobertura", ""),
            "Run_Id": r.get("run_id", ""),
            "Actualizado_En": r.get("actualizado_en", ""),
        }
        for r in origen
    ]
    return sorted(filas, key=lambda r: (str(r["Linea"]), str(r["Fecha"])))


# ---------------------------------------------------------------------------
# Publicación
# ---------------------------------------------------------------------------

def publicar(audit_dir: Path, consolidated_dir: Path) -> Dict[str, object]:
    """Escribe las tablas de auditoría junto al consolidado.

    Se llama sólo cuando el consolidado se publicó bien: reasignaciones frescas
    al lado de un consolidado abortado sería peor que no publicar nada. Esa
    decisión vive en el orquestador, que es el único que la conoce.

    Ambos archivos se escriben aunque no haya nada que publicar: un archivo con
    sólo la cabecera mantiene vivo el modelo del BI, mientras que uno ausente lo
    rompe en cada refresco.
    """
    audit_dir, consolidated_dir = Path(audit_dir), Path(consolidated_dir)

    filas_reasignaciones = reasignaciones(audit_dir)
    filas_cobertura = cobertura(audit_dir)

    path_reasignaciones = _escribir(
        filas_reasignaciones,
        consolidated_dir / REASIGNACIONES_FILENAME,
        REASIGNACIONES_COLUMNS,
    )
    path_cobertura = _escribir(
        filas_cobertura,
        consolidated_dir / COBERTURA_PUBLICADA_FILENAME,
        COBERTURA_PUBLICADA_COLUMNS,
    )

    return {
        "reasignaciones": path_reasignaciones,
        "filas_reasignaciones": len(filas_reasignaciones),
        "cobertura": path_cobertura,
        "filas_cobertura": len(filas_cobertura),
    }
