"""
Trazabilidad en la propia fila de datos.

El libro de auditoría tiene el detalle de cada cambio, pero para llegar a él
hay que saber qué buscar. Estas columnas viajan en el CSV de datos y cierran
esa distancia: desde una fila cualquiera se puede saltar a su historial, y se
puede medir cuánto tardó la planta en asignarle un código.

Columnas
--------
``record_key``          Identidad estable del segmento. Es la clave con la que
                        se une el dato al libro de auditoría.
``run_id_alta``         Corrida en la que el segmento apareció por primera vez.
``run_id_version``      Corrida que produjo el contenido actual de la fila.
                        Sólo cambia cuando la fila cambia, de modo que una
                        re-extracción idéntica deja el archivo igual. Apunta al
                        snapshot que contiene esta versión.
``reasignada``          1 si alguna vez le cambiaron el código, 0 si no.
``fecha_reasignacion``  Fecha en que se detectó la última reasignación, vacía
                        si nunca ocurrió.

El retraso de asignación se calcula como
``fecha_reasignacion - Fecha_Hora_Inicio``.

Nota sobre la fecha
-------------------
Es la fecha en que la corrida **detectó** el cambio, no aquella en que el
supervisor lo hizo: con una corrida diaria ambas coinciden con un día de
margen. No se puede saber más que eso desde afuera de la plataforma.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Mapping, Sequence, Set

from typack_etl.audit.cdc import REASIGNACION, Cambio, Contexto
from typack_etl.audit.keys import record_key

CLAVE = "record_key"
ALTA = "run_id_alta"
VERSION = "run_id_version"
REASIGNADA = "reasignada"
FECHA = "fecha_reasignacion"


def claves_cambiadas(cambios: Iterable[Cambio]) -> tuple[Set[str], Set[str]]:
    """(claves con cualquier cambio, claves reasignadas) de una corrida."""
    todas: Set[str] = set()
    reasignadas: Set[str] = set()
    for cambio in cambios:
        todas.add(cambio.record_key)
        if cambio.tipo_cambio == REASIGNACION:
            reasignadas.add(cambio.record_key)
    return todas, reasignadas


def anotar(
    nuevas: Sequence[Mapping],
    previas: Sequence[Mapping],
    cambios: Iterable[Cambio],
    ctx: Contexto,
) -> List[dict]:
    """Devuelve copias de `nuevas` con las columnas de traza.

    No modifica lo que recibe: `nuevas` son los diccionarios que devolvió la
    API y que ya se guardaron crudos en el landing. Anotarlos en el lugar
    ensuciaría la respuesta original para cualquiera que la esté mirando.

    Las filas previas aportan el alta y el historial de reasignación. Marcar
    su alta con la corrida de hoy sería mentir sobre cuándo aparecieron, así
    que lo que no se sabe queda vacío.
    """
    anteriores: Dict[str, Mapping] = {}
    for fila in previas:
        clave = fila.get(CLAVE) or record_key(ctx.devid, fila)
        anteriores.setdefault(clave, fila)

    con_cambio, reasignadas = claves_cambiadas(cambios)
    fecha_hoy = ctx.detectado_en.strftime("%Y-%m-%d")

    anotadas: List[dict] = []
    for original in nuevas:
        fila = dict(original)
        clave = record_key(ctx.devid, fila)
        previa = anteriores.get(clave)
        fila[CLAVE] = clave

        if previa is None:
            fila[ALTA] = ctx.run_id
            fila[VERSION] = ctx.run_id
            fila[REASIGNADA] = 0
            fila[FECHA] = ""
        else:
            fila[ALTA] = previa.get(ALTA) or ctx.run_id
            # La versión sólo avanza si la fila efectivamente cambió: así una
            # re-extracción idéntica deja el archivo igual.
            fila[VERSION] = ctx.run_id if clave in con_cambio else (previa.get(VERSION) or ctx.run_id)
            if clave in reasignadas:
                fila[REASIGNADA] = 1
                fila[FECHA] = fecha_hoy
            else:
                fila[REASIGNADA] = _entero(previa.get(REASIGNADA))
                fila[FECHA] = previa.get(FECHA) or ""
        anotadas.append(fila)
    return anotadas


def rellenar_claves(filas: Sequence[Mapping], devid) -> List[dict]:
    """Copias de `filas` con la `record_key` asegurada.

    Las filas que se conservan sin re-extraerse también necesitan la clave:
    sin ella, lo escrito antes de que existiera la traza quedaría para siempre
    sin la columna con la que se une al libro.
    """
    completadas = []
    for original in filas:
        fila = dict(original)
        if not fila.get(CLAVE):
            fila[CLAVE] = record_key(devid, fila)
        completadas.append(fila)
    return completadas


def _entero(valor) -> int:
    try:
        return int(str(valor).strip() or 0)
    except (TypeError, ValueError):
        return 0
