"""Detector del reloj del proveedor: `duration` contra el tiempo real.

Por qué hace falta
------------------
Todo el modelo temporal del ETL se sostiene en una inferencia: que el servidor
del proveedor usa las reglas de horario de verano que Chile dejó en 2018
(`date_utils.RELOJ_API`). Nadie nos lo confirmó — los timestamps vienen sin
offset y el proveedor no documenta su zona horaria. Si algún día actualizan la
tabla de zonas horarias de su servidor, el modelo queda mal y **nada cambia
visiblemente**: los datos se corren una hora y se nota semanas después, cuando
un mes ya se publicó mal.

El testigo
----------
La API manda dos cosas redundantes por segmento: el intervalo (`start`, `end`,
en el reloj del proveedor) y `duration`, que es tiempo transcurrido real. En un
segmento normal coinciden. Si el reloj del proveedor salta **dentro** del
segmento, no: el turno de noche que cruza el cambio de hora mide 10 horas de
`duration` pero 11 de reloj.

Ahí está la prueba. Convertir los dos extremos a UTC con nuestro modelo y
comparar contra `duration` verifica el modelo sin depender de él: si el
proveedor salta cuando nosotros no lo esperamos, o no salta cuando sí, la
cuenta no cierra.

Es el mismo tipo de comprobación que hace `scripts/verificar_horario.py` con el
campo `turn`, pero sirve dentro de la corrida porque no necesita leer el
histórico: se calcula fila por fila sobre lo que la API acaba de devolver.

Calibración
-----------
Sobre las 347.478 filas de 2024-2026 con intervalo utilizable, **cero
desajustes**, incluidos los 58 segmentos que cruzan un salto del reloj (los
cuatro cambios de hora del proveedor en tres años, ±60 min cada uno). No tiene
falsos positivos conocidos: si dispara, algo cambió de verdad.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, List, Mapping

from typack_etl.extractor.utils.date_utils import a_utc, parse_api_datetime

# Los `duration` vienen con decimales y a veces redondeados al segundo. Un
# desajuste real es de una hora; el umbral sólo tiene que dejar pasar el ruido.
TOLERANCIA_MINUTOS = 1.0


@dataclass
class Desajuste:
    """Un segmento en que `duration` y el modelo temporal no coinciden."""

    linea: str
    start: str
    end: str
    duration: float
    esperado: float
    delta: float

    def __str__(self) -> str:
        return (
            f"{self.linea} {self.start} -> {self.end}: "
            f"duration={self.duration:.1f} min, el modelo predice {self.esperado:.1f} "
            f"({self.delta:+.1f})"
        )


def _minutos(valor) -> float | None:
    try:
        return float(str(valor).replace(",", "."))
    except (TypeError, ValueError):
        return None


def minutos_reales(inicio: datetime, fin: datetime) -> float:
    """Tiempo transcurrido entre dos instantes del reloj del proveedor.

    Pasa por UTC a propósito: es la única forma de que un intervalo que cruza
    un cambio de hora mida lo que duró y no lo que dice el reloj.
    """
    return (a_utc(fin) - a_utc(inicio)).total_seconds() / 60


def desajustes(rows: Iterable[Mapping], linea: str = "") -> List[Desajuste]:
    """Segmentos cuyo `duration` no cuadra con el modelo temporal.

    Se saltean las filas sin intervalo utilizable (sin `end`, con `end` previo
    a `start`, o con `duration` ilegible): no son comparables, y tratarlas como
    desajuste llenaría de ruido una alarma que sólo sirve si es silenciosa.
    """
    fuera = []
    for row in rows:
        inicio = parse_api_datetime(row.get("start"))
        fin = parse_api_datetime(row.get("end"))
        duracion = _minutos(row.get("duration"))
        if inicio is None or fin is None or duracion is None or fin < inicio:
            continue

        esperado = minutos_reales(inicio, fin)
        delta = duracion - esperado
        if abs(delta) > TOLERANCIA_MINUTOS:
            fuera.append(Desajuste(
                linea=linea,
                start=inicio.strftime("%Y-%m-%d %H:%M:%S"),
                end=fin.strftime("%Y-%m-%d %H:%M:%S"),
                duration=duracion,
                esperado=esperado,
                delta=delta,
            ))
    return fuera


def diagnostico(fuera: List[Desajuste]) -> str:
    """Qué hacer con lo que se encontró, para que quede en el log de la corrida."""
    if not fuera:
        return ""
    de_una_hora = sum(1 for d in fuera if abs(abs(d.delta) - 60) <= TOLERANCIA_MINUTOS)
    if de_una_hora == len(fuera):
        return (
            "Todos los desajustes son de una hora exacta: el reloj del proveedor "
            "saltó en un momento que el modelo no esperaba. Lo más probable es que "
            "hayan actualizado la tabla de zonas horarias de su servidor.\n"
            "  Revisar `date_utils.api_en_verano` contra las fechas de arriba y "
            "correr `scripts/verificar_horario.py --raw` antes de publicar."
        )
    return (
        "Hay desajustes que no son de una hora, así que no es un cambio de hora: "
        "revisar si el proveedor cambió cómo calcula `duration`."
    )
