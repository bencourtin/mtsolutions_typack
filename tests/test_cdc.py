"""Motor CDC: identidad, huellas, clasificación e idempotencia (fase 3)."""

import csv
import glob
import os
from datetime import datetime

import pytest

from conftest import PAR_IDENTICO, SOLAPE, VENTANA_A, VENTANA_B, FIXTURES, hay_fixture, segmento
from typack_etl.audit import cdc
from typack_etl.audit.keys import (
    assignment_hash,
    campos_distintos,
    content_hash,
    normalizar,
    record_key,
    se_solapan,
)
from typack_etl.extractor.functions.io import read_csv_rows, write_csv


def dt(texto):
    return datetime.strptime(texto, "%Y-%m-%d %H:%M:%S")


VENTANA = (dt("2026-03-01 00:00:00"), dt("2026-03-31 23:59:59"))
CTX = cdc.Contexto(
    run_id="r2", devid=1273, linea="TF_8",
    detectado_en=dt("2026-03-25 07:00:00"), run_id_anterior="r1",
)


@pytest.fixture
def base():
    """Cinco días de marzo, uno por día."""
    return [segmento(f"2026-03-{d:02d} 08:00:00", f"2026-03-{d:02d} 09:00:00",
                     code_description="Micro parada", code_t="Micro parada")
            for d in range(1, 6)]


def comparar(previas, nuevas, **kw):
    return cdc.comparar(previas, nuevas, CTX, *VENTANA, **kw)


# --------------------------------------------------------------------------
# Identidad y huellas
# --------------------------------------------------------------------------

def test_la_clave_distingue_dos_segmentos_con_el_mismo_inicio():
    """Una micro-parada de 0 min convive con una detención en el mismo instante."""
    normal = segmento("2026-03-10 08:00:00", "2026-03-10 09:00:00")
    cero = segmento("2026-03-10 08:00:00", "2026-03-10 08:00:00")
    assert record_key(1273, normal) != record_key(1273, cero)


def test_la_clave_distingue_lineas():
    fila = segmento("2026-03-10 08:00:00", "2026-03-10 09:00:00")
    assert record_key(1273, fila) != record_key(1274, fila)


def test_reasignar_cambia_la_huella_de_asignacion(base):
    original = base[0]
    reasignado = dict(original, code_description="10.- No planificado", code_t="Operación")
    assert assignment_hash(original) != assignment_hash(reasignado)
    assert content_hash(original) != content_hash(reasignado)


def test_editar_un_comentario_no_cuenta_como_reasignacion(base):
    original = base[0]
    editado = dict(original, comments="ajuste de molde")
    assert assignment_hash(original) == assignment_hash(editado)
    assert content_hash(original) != content_hash(editado)


def test_la_huella_no_depende_del_origen_del_dato(base):
    """Un lado viene de la API (float, int) y el otro de un CSV (texto)."""
    api = dict(base[0], duration=4.366666666666666, production=50, order=2412.0)
    desde_csv = {k: normalizar(api, k) for k in api}
    assert content_hash(api) == content_hash(desde_csv)


def test_campos_distintos_lista_lo_que_cambio(base):
    otro = dict(base[0], code_description="05.- Setup", comments="x")
    assert campos_distintos(base[0], otro) == ["code_description", "comments"]


@pytest.mark.parametrize("a,b,solapan", [
    (("08:00:00", "09:00:00"), ("08:30:00", "10:00:00"), True),
    (("08:00:00", "09:00:00"), ("09:00:00", "10:00:00"), False),   # contiguos
    (("08:00:00", "09:00:00"), ("10:00:00", "11:00:00"), False),
    (("08:00:00", "12:00:00"), ("09:00:00", "10:00:00"), True),    # contenido
])
def test_se_solapan(a, b, solapan):
    x = segmento(f"2026-03-10 {a[0]}", f"2026-03-10 {a[1]}")
    y = segmento(f"2026-03-10 {b[0]}", f"2026-03-10 {b[1]}")
    assert se_solapan(x, y) is solapan


# --------------------------------------------------------------------------
# Clasificación
# --------------------------------------------------------------------------

def test_cambiar_el_codigo_es_una_reasignacion(base):
    nuevas = [dict(base[0], code_description="05.- Setup", code_t="Setup")] + base[1:]
    r = comparar(base, nuevas)

    assert r.resumen[cdc.REASIGNACION] == 1
    assert r.resumen[cdc.OTRO_CAMBIO] == 0
    assert {c.campo for c in r.cambios} == {"code_description", "code_t"}
    cambio = next(c for c in r.cambios if c.campo == "code_description")
    assert (cambio.valor_anterior, cambio.valor_nuevo) == ("Micro parada", "05.- Setup")
    assert cambio.dias_desde_evento == 24
    assert (cambio.run_id_anterior, cambio.run_id_actual) == ("r1", "r2")


def test_cambiar_solo_el_comentario_es_otro_cambio(base):
    r = comparar(base, [dict(base[0], comments="se corre el corte")] + base[1:])
    assert r.resumen[cdc.OTRO_CAMBIO] == 1
    assert r.resumen[cdc.REASIGNACION] == 0


def test_un_segmento_inedito_es_un_alta(base):
    nuevo = segmento("2026-03-06 08:00:00", "2026-03-06 09:00:00")
    r = comparar(base, base + [nuevo])
    assert r.resumen[cdc.NUEVO] == 1
    assert r.cambios == [], "los altas no deben ensuciar el libro"


def test_los_altas_se_pueden_registrar_si_se_piden(base):
    nuevo = segmento("2026-03-06 08:00:00", "2026-03-06 09:00:00")
    r = comparar(base, base + [nuevo], registrar_nuevos=True)
    assert len(r.cambios) == 1
    assert r.cambios[0].tipo_cambio == cdc.NUEVO


def test_un_segmento_que_deja_de_reportarse_desaparece(base):
    r = comparar(base, base[1:])
    assert r.resumen[cdc.DESAPARECIDO] == 1
    assert [c.tipo_cambio for c in r.cambios] == [cdc.DESAPARECIDO]


def test_un_segmento_partido_en_dos_es_un_recorte(base):
    partido = [
        segmento("2026-03-01 08:00:00", "2026-03-01 08:30:00"),
        segmento("2026-03-01 08:30:00", "2026-03-01 09:00:00"),
    ]
    r = comparar(base, partido + base[1:])

    assert r.resumen[cdc.RECORTE_SEGMENTO] == 1
    assert r.resumen[cdc.DESAPARECIDO] == 0
    assert r.resumen[cdc.NUEVO] == 0, "los destinos del recorte no son altas"
    assert "08:30:00" in r.cambios[0].valor_nuevo, "debe registrar el mapeo origen -> destino"


def test_dos_micro_paradas_en_el_mismo_instante_no_se_descartan(base):
    """Observado en producción: pasa en segmentos de duración cero."""
    gemelas = [
        segmento("2026-03-10 09:41:07", "2026-03-10 09:41:07", duration="0.0",
                 code_description="Micro parada", code_t="Micro parada", user="LUIS FLORES"),
        segmento("2026-03-10 09:41:07", "2026-03-10 09:41:07", duration="0.0"),
    ]
    r = comparar(base + gemelas, base + gemelas)
    assert r.cambios == []
    assert r.resumen["colisiones_clave"] == 2, "una por lado, ambas conservadas"


def test_el_orden_entre_gemelas_no_inventa_una_reasignacion():
    """Descartar la segunda dependería del orden de la API, que no es estable."""
    gemelas = [
        segmento("2026-03-10 09:41:07", "2026-03-10 09:41:07", duration="0.0",
                 code_description="Micro parada", code_t="Micro parada"),
        segmento("2026-03-10 09:41:07", "2026-03-10 09:41:07", duration="0.0"),
    ]
    assert comparar(gemelas, list(reversed(gemelas))).cambios == []


def test_si_una_gemela_cambia_se_reporta_el_grupo():
    antes = [
        segmento("2026-03-10 09:41:07", "2026-03-10 09:41:07", duration="0.0",
                 code_description="Micro parada", code_t="Micro parada"),
        segmento("2026-03-10 09:41:07", "2026-03-10 09:41:07", duration="0.0"),
    ]
    despues = [
        antes[0],
        dict(antes[1], code_description="05.- Setup", code_t="Setup"),
    ]
    r = comparar(antes, despues)

    assert r.resumen[cdc.REASIGNACION] == 1
    assert len(r.cambios) == 1
    cambio = r.cambios[0]
    assert cambio.campo == "grupo_instante_unico"
    assert "05.- Setup" in cambio.valor_nuevo
    assert "Micro parada" in cambio.valor_anterior


def test_dos_filas_identicas_no_son_un_cambio():
    """La API a veces devuelve la misma fila dos veces."""
    fila = segmento("2026-03-10 09:41:07", "2026-03-10 09:41:07", duration="0.0")
    assert comparar([fila, dict(fila)], [fila, dict(fila)]).cambios == []


def test_una_clave_dada_por_perdida_que_vuelve_es_un_reaparecido(base):
    perdida = record_key(1273, base[0])
    r = comparar(base[1:], base, ausentes_conocidas={perdida})
    assert r.resumen[cdc.REAPARECIDO] == 1


# --------------------------------------------------------------------------
# Alcance de la comparación
# --------------------------------------------------------------------------

def test_lo_que_quedo_fuera_de_la_ventana_no_se_da_por_borrado(base):
    """No se preguntó por ello: no es un segmento borrado."""
    fuera = segmento("2026-02-15 08:00:00", "2026-02-15 09:00:00")
    r = comparar(base + [fuera], base)
    assert r.resumen[cdc.DESAPARECIDO] == 0


def test_el_segmento_del_borde_se_compara_y_no_se_cuenta_como_alta(base):
    """La API devuelve el turno completo que contiene el borde de la ventana."""
    borde = segmento("2026-02-28 22:00:00", "2026-03-01 06:00:00")
    r = comparar(base + [borde], base + [borde])
    assert r.resumen[cdc.NUEVO] == 0
    assert r.cambios == []


# --------------------------------------------------------------------------
# Idempotencia
# --------------------------------------------------------------------------

def test_comparar_algo_consigo_mismo_no_produce_cambios(base):
    r = comparar(base, list(base))
    assert r.cambios == []


def test_el_orden_de_la_respuesta_no_importa(base):
    """La API no garantiza orden entre extracciones idénticas."""
    barajado = list(reversed(base))
    assert comparar(base, barajado).cambios == []


def test_ida_y_vuelta_por_csv_no_produce_cambios(tmp_path, base):
    """Los floats de la API contra el texto del CSV ya escrito."""
    api = [dict(f, duration=float(f["duration"]), production=int(f["production"])) for f in base]
    destino = tmp_path / "particion.csv"
    write_csv(api, destino)
    assert comparar(read_csv_rows(destino), api).cambios == []


# --------------------------------------------------------------------------
# Casos de oro sobre datos reales del repositorio
# --------------------------------------------------------------------------

def test_reextraer_el_mismo_periodo_no_detecta_nada():
    hay_fixture(*PAR_IDENTICO)
    with PAR_IDENTICO[0].open(encoding="utf-8") as f:
        antes = list(csv.DictReader(f))
    with PAR_IDENTICO[1].open(encoding="utf-8") as f:
        despues = list(csv.DictReader(f))

    ctx = cdc.Contexto(run_id="r2", devid=1273, linea="TF_8", detectado_en=dt("2026-08-26 10:00:00"))
    ventana = (dt("2026-04-01 12:00:00"), dt("2026-05-01 12:00:00"))
    r = cdc.comparar(antes, despues, ctx, *ventana)

    assert r.cambios == []
    assert r.resumen[cdc.NUEVO] == 0
    assert r.resumen["colisiones_clave"] == 0


def _pares_solapados():
    for a in sorted(glob.glob(str(FIXTURES / "processed" / (VENTANA_A + "*.csv")))):
        linea = os.path.basename(a).split("23_59_59_")[1].replace(".csv", "")
        b = FIXTURES / "processed" / (VENTANA_B + linea + ".csv")
        if b.exists():
            yield linea, a, str(b)


def test_reproduce_las_34_reasignaciones_del_analisis():
    """Solape 22-feb -> 09-mar entre las extracciones del 10-mar y el 24-mar."""
    pares = list(_pares_solapados())
    if not pares:
        pytest.skip("faltan los pares de ventanas solapadas en data/processed")

    ini, fin = dt(SOLAPE[0]), dt(SOLAPE[1])
    total = {}
    reasignaciones = []
    for linea, a, b in pares:
        nuevas = [r for r in read_csv_rows(b) if cdc.en_ventana(r, ini, fin)]
        ctx = cdc.Contexto(run_id="r2", devid=linea.rsplit("_", 1)[1], linea=linea,
                           detectado_en=dt("2026-03-24 10:38:00"), run_id_anterior="r1")
        r = cdc.comparar(read_csv_rows(a), nuevas, ctx, ini, fin)
        for k, v in r.resumen.items():
            total[k] = total.get(k, 0) + v
        reasignaciones += [c for c in r.cambios if c.tipo_cambio == cdc.REASIGNACION]

    assert total[cdc.REASIGNACION] == 34
    assert total[cdc.RECORTE_SEGMENTO] == 147
    assert total[cdc.DESAPARECIDO] == 0
    assert total["colisiones_clave"] == 0
    assert sum(1 for c in reasignaciones if c.campo == "code_description") == 34


def test_las_reasignaciones_caen_dentro_de_la_ventana_de_30_dias():
    """Sostiene la elección de 30 días como ventana observada."""
    pares = list(_pares_solapados())
    if not pares:
        pytest.skip("faltan los pares de ventanas solapadas en data/processed")

    ini, fin = dt(SOLAPE[0]), dt(SOLAPE[1])
    dias = set()
    for linea, a, b in pares:
        nuevas = [r for r in read_csv_rows(b) if cdc.en_ventana(r, ini, fin)]
        ctx = cdc.Contexto(run_id="r2", devid=linea.rsplit("_", 1)[1], linea=linea,
                           detectado_en=dt("2026-03-24 10:38:00"))
        r = cdc.comparar(read_csv_rows(a), nuevas, ctx, ini, fin)
        dias |= {c.dias_desde_evento for c in r.cambios if c.tipo_cambio == cdc.REASIGNACION}

    assert dias, "no se detectó ninguna reasignación"
    assert max(dias) <= 30
