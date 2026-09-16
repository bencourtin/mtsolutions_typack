"""
Motor CDC: compara una extracción nueva contra lo que ya había registrado.

Alcance de la comparación
-------------------------
Sólo se comparan los segmentos que caen dentro de la ventana consultada. Un
segmento que quedó fuera del rango no es un segmento borrado: es un segmento
que no se preguntó. Confundir ambos casos convertiría cada corrida diaria en
cientos de falsos DESAPARECIDO.

Por qué el orden no importa
---------------------------
La API no garantiza orden: dos extracciones idénticas del mismo período
devuelven las mismas filas en distinto orden. La comparación se hace por
`record_key` sobre diccionarios, nunca por posición, de modo que reordenar la
respuesta no produce ningún cambio.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Iterable, List, Mapping, Sequence, Set

from typack_etl.audit.keys import (
    ASSIGNMENT_FIELDS,
    assignment_hash,
    campos_distintos,
    content_hash,
    normalizar,
    record_key,
    se_solapan,
)
from typack_etl.extractor.functions.io import solapa_ventana
from typack_etl.extractor.utils.date_utils import parse_api_datetime

# Tipos de cambio
NUEVO = "NUEVO"
REASIGNACION = "REASIGNACION"
RECORTE_SEGMENTO = "RECORTE_SEGMENTO"
DESAPARECIDO = "DESAPARECIDO"
REAPARECIDO = "REAPARECIDO"
OTRO_CAMBIO = "OTRO_CAMBIO"


@dataclass
class Contexto:
    """Datos de la corrida que acompañan a cada cambio detectado."""

    run_id: str
    devid: object
    linea: str
    detectado_en: datetime
    run_id_anterior: str = ""


@dataclass
class Cambio:
    """Una fila del libro de auditoría: un campo de un segmento, o un evento."""

    record_key: str
    devid: object
    linea: str
    maquina: str
    start: str
    end: str
    duration: str
    detectado_en: str
    dias_desde_evento: int
    run_id_anterior: str
    run_id_actual: str
    tipo_cambio: str
    campo: str
    valor_anterior: str
    valor_nuevo: str
    usuario_anterior: str
    usuario_nuevo: str


@dataclass
class Resultado:
    """Cambios detectados más el resumen de la comparación."""

    cambios: List[Cambio] = field(default_factory=list)
    resumen: Dict[str, int] = field(default_factory=dict)
    claves_desaparecidas: Set[str] = field(default_factory=set)
    claves_reaparecidas: Set[str] = field(default_factory=set)


def en_ventana(row: Mapping, inicio: datetime, fin: datetime) -> bool:
    """True si el segmento cae dentro de la ventana consultada.

    Delega en el mismo criterio que usa el writer: si el CDC y la escritura
    discreparan sobre qué es "estar en la ventana", el libro reportaría altas
    y bajas que el archivo no tiene.
    """
    return solapa_ventana(row, inicio, fin)


def _indexar(rows: Iterable[Mapping], devid) -> tuple[Dict[str, List[Mapping]], int]:
    """Agrupa las filas por record_key.

    La clave `(devid, start, end)` es única salvo en los segmentos de duración
    cero, donde la planta puede registrar dos micro-paradas en el mismo
    instante. Observado en producción: 2 casos en 9.074 filas.

    Esas filas NO se descartan. Descartarlas dependería del orden en que la API
    las devuelva, que no está garantizado, y bastaría con que llegaran al revés
    para inventar una reasignación. Se conservan todas y el grupo se compara
    como conjunto.

    Retorna (índice, cantidad de filas que comparten clave con otra).
    """
    index: Dict[str, List[Mapping]] = {}
    colisiones = 0
    for row in rows:
        key = record_key(devid, row)
        if key in index:
            colisiones += 1
        index.setdefault(key, []).append(row)
    return index, colisiones


def _grupo_equivalente(anteriores: Sequence[Mapping], actuales: Sequence[Mapping]) -> bool:
    """True si dos grupos de filas que comparten clave tienen el mismo contenido."""
    return sorted(content_hash(r) for r in anteriores) == sorted(content_hash(r) for r in actuales)


def comparar(
    previas: Sequence[Mapping],
    nuevas: Sequence[Mapping],
    ctx: Contexto,
    ventana_inicio: datetime,
    ventana_fin: datetime,
    ausentes_conocidas: Set[str] | None = None,
    registrar_nuevos: bool = False,
) -> Resultado:
    """Compara el tramo de ventana de `previas` contra `nuevas`.

    Args:
        previas: filas ya almacenadas para esta línea y partición.
        nuevas: filas que devolvió la API en esta corrida.
        ausentes_conocidas: record_keys que el libro ya tiene como
            DESAPARECIDO; sirven para distinguir un REAPARECIDO de un NUEVO.
        registrar_nuevos: por defecto los altas no se escriben al libro. Un
            alta no es una mutación de un hecho conocido y en una ventana de 35
            días son cientos por línea y por día, que duplicarían los archivos
            de datos. Se cuentan en el resumen igual.
    """
    ausentes_conocidas = ausentes_conocidas or set()

    nuevas_index, colisiones_nuevas = _indexar(nuevas, ctx.devid)

    # El alcance de la comparación es aquello sobre lo que la API es autoridad:
    # la ventana consultada, más cualquier segmento que la respuesta traiga aunque
    # caiga fuera (la API devuelve el turno completo que contiene el borde). Sin
    # esta segunda condición, esos segmentos del borde se contarían como altas
    # nuevas en cada corrida.
    prev_ventana = [
        r for r in previas
        if en_ventana(r, ventana_inicio, ventana_fin) or record_key(ctx.devid, r) in nuevas_index
    ]
    prev_index, colisiones_prev = _indexar(prev_ventana, ctx.devid)

    claves_prev = set(prev_index)
    claves_nuevas = set(nuevas_index)
    desaparecidas = claves_prev - claves_nuevas
    aparecidas = claves_nuevas - claves_prev

    resultado = Resultado()
    conteo = {
        NUEVO: 0, REASIGNACION: 0, RECORTE_SEGMENTO: 0,
        DESAPARECIDO: 0, REAPARECIDO: 0, OTRO_CAMBIO: 0,
    }

    # Un segmento que desaparece y cuyo intervalo queda cubierto por segmentos
    # nuevos no se perdió: se volvió a cortar (merge o split).
    # Una clave puede agrupar más de una fila (segmentos de duración cero en el
    # mismo instante). Para ubicar y describir el segmento basta la primera:
    # todas comparten línea, inicio y fin.
    def repr_prev(key):
        return prev_index[key][0]

    def repr_nueva(key):
        return nuevas_index[key][0]

    recortadas: Set[str] = set()
    destino_de: Dict[str, List[str]] = {}
    for key in desaparecidas:
        origen = repr_prev(key)
        destinos = [k for k in aparecidas if se_solapan(origen, repr_nueva(k))]
        if destinos:
            recortadas.add(key)
            destino_de[key] = destinos

    for key in sorted(desaparecidas, key=lambda k: normalizar(repr_prev(k), "start")):
        row = repr_prev(key)
        if key in recortadas:
            destinos = destino_de[key]
            detalle = "; ".join(
                f"{normalizar(repr_nueva(d), 'start')} -> {normalizar(repr_nueva(d), 'end')}"
                for d in sorted(destinos, key=lambda d: normalizar(repr_nueva(d), "start"))
            )
            conteo[RECORTE_SEGMENTO] += 1
            resultado.cambios.append(
                _cambio(row, row, ctx, RECORTE_SEGMENTO, "intervalo",
                        f"{normalizar(row, 'start')} -> {normalizar(row, 'end')}", detalle)
            )
        else:
            conteo[DESAPARECIDO] += 1
            resultado.claves_desaparecidas.add(key)
            resultado.cambios.append(
                _cambio(row, row, ctx, DESAPARECIDO, "",
                        normalizar(row, "code_description"), "")
            )

    destinos_de_recorte = {d for destinos in destino_de.values() for d in destinos}

    for key in sorted(claves_nuevas, key=lambda k: normalizar(repr_nueva(k), "start")):
        actual = repr_nueva(key)
        anterior = repr_prev(key) if key in prev_index else None

        if anterior is None:
            if key in destinos_de_recorte:
                continue  # ya quedó registrado como destino del recorte
            if key in ausentes_conocidas:
                conteo[REAPARECIDO] += 1
                resultado.claves_reaparecidas.add(key)
                resultado.cambios.append(
                    _cambio(actual, actual, ctx, REAPARECIDO, "",
                            "", normalizar(actual, "code_description"))
                )
            else:
                conteo[NUEVO] += 1
                if registrar_nuevos:
                    resultado.cambios.append(
                        _cambio(actual, actual, ctx, NUEVO, "",
                                "", normalizar(actual, "code_description"))
                    )
            continue

        grupo_prev, grupo_actual = prev_index[key], nuevas_index[key]

        if len(grupo_prev) > 1 or len(grupo_actual) > 1:
            # Varios segmentos de duración cero en el mismo instante: no hay
            # forma de aparearlos uno a uno, así que se compara el grupo
            # completo. Si algo cambió se reporta el grupo, sin atribuir el
            # cambio a un segmento en particular.
            if _grupo_equivalente(grupo_prev, grupo_actual):
                continue
            reasignado = sorted(assignment_hash(r) for r in grupo_prev) != sorted(
                assignment_hash(r) for r in grupo_actual
            )
            tipo = REASIGNACION if reasignado else OTRO_CAMBIO
            conteo[tipo] += 1
            resultado.cambios.append(
                _cambio(actual, anterior, ctx, tipo, "grupo_instante_unico",
                        " | ".join(sorted(normalizar(r, "code_description") for r in grupo_prev)),
                        " | ".join(sorted(normalizar(r, "code_description") for r in grupo_actual)))
            )
            continue

        if content_hash(anterior) == content_hash(actual):
            continue

        reasignado = assignment_hash(anterior) != assignment_hash(actual)
        tipo = REASIGNACION if reasignado else OTRO_CAMBIO
        conteo[tipo] += 1
        for campo in campos_distintos(anterior, actual):
            resultado.cambios.append(
                _cambio(actual, anterior, ctx, tipo, campo,
                        normalizar(anterior, campo), normalizar(actual, campo))
            )

    resultado.resumen = {
        **conteo,
        "previas_en_ventana": len(prev_ventana),
        "nuevas": len(nuevas),
        "colisiones_clave": colisiones_prev + colisiones_nuevas,
        "filas_libro": len(resultado.cambios),
    }
    return resultado


def _cambio(
    row: Mapping,
    row_anterior: Mapping,
    ctx: Contexto,
    tipo: str,
    campo: str,
    valor_anterior: str,
    valor_nuevo: str,
) -> Cambio:
    started = parse_api_datetime(row.get("start"))
    dias = (ctx.detectado_en.date() - started.date()).days if started else -1
    return Cambio(
        record_key=record_key(ctx.devid, row),
        devid=ctx.devid,
        linea=ctx.linea,
        maquina=normalizar(row, "device"),
        start=normalizar(row, "start"),
        end=normalizar(row, "end"),
        duration=normalizar(row, "duration"),
        detectado_en=ctx.detectado_en.strftime("%Y-%m-%d %H:%M:%S"),
        dias_desde_evento=dias,
        run_id_anterior=ctx.run_id_anterior,
        run_id_actual=ctx.run_id,
        tipo_cambio=tipo,
        campo=campo,
        valor_anterior=valor_anterior,
        valor_nuevo=valor_nuevo,
        usuario_anterior=normalizar(row_anterior, "user"),
        usuario_nuevo=normalizar(row, "user"),
    )


def es_reasignacion_de_codigo(cambio: Cambio) -> bool:
    """True si la fila del libro corresponde a un código de detención."""
    return cambio.tipo_cambio == REASIGNACION and cambio.campo in ASSIGNMENT_FIELDS
