"""
Identidad y huellas de un segmento de la sábana.

La API no devuelve ningún `id`, así que la identidad se deriva del segmento
físico. Verificado sobre 26.776 filas reales: `start` por sí solo colisiona en
0,17 % de los casos (una micro-parada de 0 min convive con una detención de
5,8 min en el mismo instante), mientras que `(devid, start, end)` es única.

Las dos huellas separan lo que se audita de lo que sólo se registra:

  - `assignment_hash` cubre los códigos de detención. Si cambia, alguien
    reclasificó la detención: es el evento que se quiere detectar.
  - `content_hash` cubre además el resto de los campos de negocio. Si cambia
    sin que cambie el de asignación, se editó un comentario, un usuario o una
    cantidad producida.

Normalización
-------------
Un lado de la comparación viene de la API (floats, ints, claves ausentes) y el
otro de un CSV ya escrito (todo texto, decimales con coma). Sin normalizar,
`4.366666666666666` y `"4,366666666666666"` serían distintos y toda corrida
marcaría todos los registros como cambiados. `normalizar()` aplica el mismo
`format_value` que usa el writer, de modo que ambos lados se comparan en la
representación en que quedan almacenados.
"""

from __future__ import annotations

import hashlib
from typing import Mapping, Sequence, Tuple

from typack_etl.extractor.functions.io import format_value
from typack_etl.extractor.utils.date_utils import parse_api_datetime

# Separador improbable dentro de un valor, para que la concatenación sea inyectiva.
_SEP = "\x1f"

# Códigos de detención: lo que cambia cuando se reasigna una detención.
ASSIGNMENT_FIELDS: Tuple[str, ...] = (
    "code_description",
    "code_u",
    "code_f",
    "code_t",
    "code_l",
    "code_m",
    "code_n",
)

# Resto de los campos de negocio del segmento.
CONTEXT_FIELDS: Tuple[str, ...] = (
    "comments",
    "user",
    "production",
    "duration",
    "order",
    "sku",
    "product",
    "turn",
    "device",
    "line",
    "plant",
    "format",
)

CONTENT_FIELDS: Tuple[str, ...] = ASSIGNMENT_FIELDS + CONTEXT_FIELDS


def normalizar(row: Mapping, field: str) -> str:
    """Valor de un campo en la representación en que queda almacenado."""
    return str(format_value(row.get(field))).strip()


def _hash(row: Mapping, fields: Sequence[str]) -> str:
    payload = _SEP.join(normalizar(row, f) for f in fields)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def record_key(devid, row: Mapping) -> str:
    """Identidad del segmento: sha1 de devid + start + end."""
    payload = _SEP.join((str(devid), normalizar(row, "start"), normalizar(row, "end")))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def assignment_hash(row: Mapping) -> str:
    """Huella de los códigos de detención."""
    return _hash(row, ASSIGNMENT_FIELDS)


def content_hash(row: Mapping) -> str:
    """Huella de todos los campos de negocio del segmento."""
    return _hash(row, CONTENT_FIELDS)


def campos_distintos(anterior: Mapping, actual: Mapping, fields: Sequence[str] = CONTENT_FIELDS):
    """Campos cuyo valor normalizado difiere, en orden estable."""
    return [f for f in fields if normalizar(anterior, f) != normalizar(actual, f)]


def intervalo(row: Mapping):
    """(inicio, fin) del segmento como datetimes, o None si no se puede leer."""
    start = parse_api_datetime(row.get("start"))
    end = parse_api_datetime(row.get("end"))
    if start is None or end is None:
        return None
    return start, end


def se_solapan(a: Mapping, b: Mapping) -> bool:
    """True si dos segmentos comparten tiempo.

    Los extremos que sólo se tocan no cuentan como solape: el fin de un
    segmento es el inicio del siguiente en una sábana continua.
    """
    ia, ib = intervalo(a), intervalo(b)
    if ia is None or ib is None:
        return False
    (ini_a, fin_a), (ini_b, fin_b) = ia, ib
    if ini_a == fin_a or ini_b == fin_b:  # segmentos de duración cero
        return ini_b <= ini_a <= fin_b or ini_a <= ini_b <= fin_a
    return ini_a < fin_b and ini_b < fin_a
