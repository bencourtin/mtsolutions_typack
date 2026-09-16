"""Clasificación de turnos según config/horarios.json (esquema v2, vigencias).

Lo que se fija acá son las reglas que el JSON no puede expresar: la selección
de vigencia por fecha, no sobrescribir lo que trajo el origen, el hueco entre
el fin de TD y el inicio de TN en Termoformado, el desempate del solape TD/TT
de Extrusión, y qué hacer con lo que el config no cubre (fin de semana y
fechas anteriores a la vigencia más antigua).
"""

from datetime import datetime

import pytest

from config.settings import load_horarios
from typack_etl.transformer.turnos import (
    Caso,
    Horarios,
    normalizar_area,
    sin_horas_extra,
)


@pytest.fixture
def horarios():
    return Horarios(load_horarios())


def dt(texto):
    return datetime.strptime(texto, "%Y-%m-%d %H:%M:%S")


# Dias de referencia, elegidos para que el dia de la semana sea explicito.
LUNES_INV = "2026-06-15"        # vigencia 2026-04-invierno
MARTES_INV = "2026-06-16"
VIERNES_INV = "2026-06-19"
SABADO_INV = "2026-06-20"
DOMINGO_INV = "2026-06-21"
LUNES_VER = "2026-09-07"        # vigencia 2026-09-verano
VIERNES_VER = "2026-09-11"


def test_los_dias_de_referencia_son_los_que_creo():
    """Si alguno no fuera el dia que dice, media suite probaria otra cosa."""
    esperado = {LUNES_INV: 0, MARTES_INV: 1, VIERNES_INV: 4, SABADO_INV: 5,
                DOMINGO_INV: 6, LUNES_VER: 0, VIERNES_VER: 4}
    for dia, weekday in esperado.items():
        assert dt(f"{dia} 12:00:00").weekday() == weekday, dia


# --------------------------------------------------------------------------
# Regla 1: selección de vigencia
# --------------------------------------------------------------------------

def test_las_vigencias_se_ordenan_de_la_mas_reciente_a_la_mas_antigua(horarios):
    assert [v.id for v in horarios.vigencias] == ["2026-09-verano", "2026-04-invierno"]


@pytest.mark.parametrize("fecha,esperada", [
    ("2026-04-01 12:00:00", "2026-04-invierno"),   # el primer dia de la vigencia
    ("2026-06-15 12:00:00", "2026-04-invierno"),
    ("2026-08-31 23:59:59", "2026-04-invierno"),   # ultimo instante antes del cambio
    ("2026-09-01 00:00:00", "2026-09-verano"),     # arranca la siguiente
    ("2027-03-01 12:00:00", "2026-09-verano"),     # sin vigencia posterior, sigue rigiendo
])
def test_rige_la_vigencia_mas_reciente_que_no_sea_posterior(horarios, fecha, esperada):
    assert horarios.vigencia_para(dt(fecha)).id == esperada


def test_antes_de_la_vigencia_mas_antigua_no_hay_ninguna(horarios):
    assert horarios.vigencia_para(dt("2026-03-31 23:59:59")) is None
    assert horarios.vigencia_para(dt("2025-06-15 12:00:00")) is None


def test_una_fila_sin_vigencia_conserva_su_turno_y_se_reporta(horarios):
    """6.015 filas del historico son anteriores al 2026-04-01.

    Inventarles un turno seria corromper el historico en silencio.
    """
    assert horarios.clasificar("TERMOFORMADO", "T0", dt("2025-06-16 20:00:00")) == ("T0", Caso.SIN_VIGENCIA)
    # Tampoco se toca una que ya traia turno.
    assert horarios.clasificar("TERMOFORMADO", "TN", dt("2025-06-16 20:00:00"))[0] == "TN"


def test_una_fila_de_verano_usa_los_horarios_de_verano(horarios):
    """El lunes TT arranca 14:30 en invierno y 13:00 en verano."""
    fmt = lambda f: tuple(x.strftime("%H:%M") for x in f)
    assert fmt(horarios.franja("TT", dt(f"{LUNES_INV} 12:00:00"))) == ("14:30", "23:00")
    assert fmt(horarios.franja("TT", dt(f"{LUNES_VER} 12:00:00"))) == ("13:00", "23:00")


def test_el_lunes_a_las_1345_solo_es_TT_en_verano(horarios):
    """Consecuencia directa de la vigencia: en invierno TT todavia no arranco.

    En los dos casos gana TD igual, porque TD cubre esa hora; lo que cambia es
    si ademas hay solape.
    """
    assert horarios.turno_por_hora("Extrusion", dt(f"{LUNES_INV} 13:45:00")) == "TD"
    assert horarios.turno_por_hora("Extrusion", dt(f"{LUNES_VER} 13:45:00")) == "TD"
    # Pasado el fin de TD la diferencia se ve: en invierno el lunes TT si cubre.
    assert horarios.turno_por_hora("Extrusion", dt(f"{LUNES_INV} 18:30:00")) == "TT"


# --------------------------------------------------------------------------
# El área
# --------------------------------------------------------------------------

@pytest.mark.parametrize("linea,esperado", [
    ("TERMOFORMADO", "Termoformado"),
    ("EXTRUSIÓN", "Extrusion"),      # los datos traen tilde, el config no
    ("  extrusión  ", "Extrusion"),
    ("OTRA COSA", None),
    ("", None),
    (None, None),
])
def test_el_area_del_dato_se_mapea_a_la_del_config(linea, esperado):
    assert normalizar_area(linea) == esperado


def test_cada_area_solo_usa_sus_turnos(horarios):
    assert horarios.permitidos("Termoformado") == ("TD", "TN")
    assert horarios.permitidos("Extrusion") == ("TD", "TT", "TN")
    assert horarios.permitidos("desconocida") == ()


def test_un_area_desconocida_no_se_toca(horarios):
    assert horarios.clasificar("LINEA NUEVA", "T0", dt(f"{LUNES_INV} 09:00:00")) == ("T0", Caso.SIN_AREA)


# --------------------------------------------------------------------------
# Reglas 3 y 4: el turno sale de la hora de inicio, y reemplaza a T0
# --------------------------------------------------------------------------

@pytest.mark.parametrize("linea,hora,esperado", [
    ("EXTRUSIÓN", "09:00:00", "TD"),
    ("EXTRUSIÓN", "18:30:00", "TT"),    # TD ya cerro, TT sigue hasta 23:00
    ("EXTRUSIÓN", "23:30:00", "TN"),
    ("TERMOFORMADO", "09:00:00", "TD"),
    ("TERMOFORMADO", "23:30:00", "TN"),
])
def test_un_T0_se_reemplaza_por_el_turno_de_su_hora(horarios, linea, hora, esperado):
    turno, caso = horarios.clasificar(linea, "T0", dt(f"{LUNES_INV} {hora}"))
    assert turno == esperado
    assert caso in (Caso.CALCULADO, Caso.HUECO)


def test_la_madrugada_del_martes_sigue_siendo_la_noche_del_lunes(horarios):
    assert horarios.resolver("EXTRUSIÓN", "T0", dt(f"{MARTES_INV} 02:00:00")) == "TN"
    assert horarios.resolver("EXTRUSIÓN", "T0", dt(f"{MARTES_INV} 07:59:59")) == "TN"
    assert horarios.resolver("EXTRUSIÓN", "T0", dt(f"{MARTES_INV} 08:00:00")) == "TD"


@pytest.mark.parametrize("sin_dato", ["T0", "-", "", "  "])
def test_todos_los_valores_sin_dato_se_recalculan(horarios, sin_dato):
    assert horarios.resolver("EXTRUSIÓN", sin_dato, dt(f"{LUNES_INV} 09:00:00")) == "TD"


def test_el_guion_de_las_filas_del_7_de_septiembre(horarios):
    """11 filas reales con turno '-' que arrancan 00:00:03: son turno Noche."""
    assert horarios.resolver("TERMOFORMADO", "-", dt("2026-09-07 00:00:03")) == "TN"


# --------------------------------------------------------------------------
# Regla 5: lo que ya vino del origen no se toca
# --------------------------------------------------------------------------

@pytest.mark.parametrize("declarado", ["TD", "TT", "TN"])
def test_un_turno_valido_no_se_recalcula(horarios, declarado):
    # 16:00 del lunes cae en el solape TD/TT: el calculo diria TD, manda el dato.
    assert horarios.clasificar("EXTRUSIÓN", declarado, dt(f"{LUNES_INV} 16:00:00")) == (declarado, Caso.ORIGEN)


def test_extrusion_declarada_TN_antes_de_las_23_se_respeta(horarios):
    """251 filas reales: el origen dice TN aunque la hora caiga en franja TT."""
    assert horarios.resolver("EXTRUSIÓN", "TN", dt(f"{LUNES_INV} 20:00:00")) == "TN"


# --------------------------------------------------------------------------
# Regla 6: no se fragmenta, decide el inicio
# --------------------------------------------------------------------------

@pytest.mark.parametrize("hora,esperado", [
    ("16:00:00", "TD"),   # empieza en Dia y termina en Tarde -> Dia
    ("22:00:00", "TT"),   # empieza en Tarde y termina en Noche -> Tarde
    ("07:00:00", "TN"),   # empieza en Noche y termina en Dia -> Noche
])
def test_un_registro_que_cruza_turnos_queda_en_el_que_empezo(horarios, hora, esperado):
    """El fin no participa: sólo la hora de inicio decide."""
    assert horarios.resolver("EXTRUSIÓN", "T0", dt(f"{MARTES_INV} {hora}")) == esperado


# --------------------------------------------------------------------------
# Regla 7: el solape TD/TT de Extrusión, que cambia según el día
# --------------------------------------------------------------------------

@pytest.mark.parametrize("dia,solape", [
    (LUNES_INV, ("14:30", "18:00")),     # el lunes de invierno TT arranca mas tarde
    (MARTES_INV, ("13:00", "18:00")),
    (VIERNES_INV, ("13:00", "15:00")),   # el viernes TD cierra a las 15:00
    (LUNES_VER, ("13:00", "18:00")),     # en verano el lunes es como el resto
])
def test_el_solape_cambia_con_el_dia_y_la_vigencia(horarios, dia, solape):
    td = horarios.franja("TD", dt(f"{dia} 12:00:00"))
    tt = horarios.franja("TT", dt(f"{dia} 12:00:00"))
    assert (tt[0].strftime("%H:%M"), td[1].strftime("%H:%M")) == solape


@pytest.mark.parametrize("hora", ["14:30:00", "16:00:00", "17:59:59"])
def test_un_T0_en_el_solape_se_desempata_a_favor_de_TD(horarios, hora):
    """Es lo que hace el proveedor: las 822 filas del solape que etiqueta dicen TD."""
    assert horarios.resolver("EXTRUSIÓN", "T0", dt(f"{LUNES_INV} {hora}")) == "TD"


# --------------------------------------------------------------------------
# Regla 9: el hueco de Termoformado
# --------------------------------------------------------------------------

def test_el_hueco_se_deriva_del_json_para_cada_dia(horarios):
    """Si mueven el fin de TD o el inicio de TN, el hueco se mueve solo."""
    for dia in (LUNES_INV, VIERNES_INV, LUNES_VER, VIERNES_VER):
        momento = dt(f"{dia} 12:00:00")
        td, tn = horarios.franja("TD", momento), horarios.franja("TN", momento)
        assert horarios.hueco(momento) == (td[1], tn[0]), dia


@pytest.mark.parametrize("dia,hueco", [
    (LUNES_INV, ("18:00", "23:00")),
    (VIERNES_INV, ("15:00", "21:30")),
    (VIERNES_VER, ("15:00", "20:00")),
])
def test_el_hueco_se_corre_con_la_jornada_corta(horarios, dia, hueco):
    assert tuple(x.strftime("%H:%M") for x in horarios.hueco(dt(f"{dia} 12:00:00"))) == hueco


@pytest.mark.parametrize("hora", ["18:00:00", "20:00:00", "22:59:59"])
def test_una_detencion_en_el_hueco_es_turno_dia(horarios, hora):
    assert horarios.clasificar("TERMOFORMADO", "T0", dt(f"{LUNES_INV} {hora}")) == ("TD", Caso.HUECO)


@pytest.mark.parametrize("hora", ["18:00:00", "20:00:00", "22:59:59"])
def test_el_hueco_pisa_incluso_un_turno_que_vino_del_origen(horarios, hora):
    """405 filas reales vienen declaradas TN arrancando en el hueco.

    Es la única excepción a la regla de no sobrescribir.
    """
    assert horarios.resolver("TERMOFORMADO", "TN", dt(f"{LUNES_INV} {hora}")) == "TD"


def test_el_hueco_no_aplica_a_extrusion(horarios):
    """En Extrusión esa franja es TT, no un hueco."""
    assert horarios.resolver("EXTRUSIÓN", "T0", dt(f"{LUNES_INV} 20:00:00")) == "TT"


def test_el_viernes_a_las_2230_termoformado_ya_es_noche(horarios):
    """El viernes TN arranca 21:30, asi que esa hora deja de ser hueco."""
    assert horarios.resolver("TERMOFORMADO", "T0", dt(f"{VIERNES_INV} 22:30:00")) == "TN"
    assert horarios.resolver("TERMOFORMADO", "T0", dt(f"{LUNES_INV} 22:30:00")) == "TD"


# --------------------------------------------------------------------------
# Regla 8: el rollover de TN, incluido el viernes
# --------------------------------------------------------------------------

@pytest.mark.parametrize("inicio,fin_esperado", [
    (f"{LUNES_INV} 23:30:00", f"{MARTES_INV} 08:00:00"),
    (f"{MARTES_INV} 02:00:00", f"{MARTES_INV} 08:00:00"),  # ya es madrugada
    (f"{VIERNES_INV} 22:00:00", f"{SABADO_INV} 08:30:00"),  # viernes a sabado, 08:30
    (f"{VIERNES_VER} 21:00:00", "2026-09-12 07:00:00"),     # viernes de verano, 07:00
])
def test_el_fin_de_TN_cae_al_dia_correcto(horarios, inicio, fin_esperado):
    assert horarios.fin_de_turno("TERMOFORMADO", "TN", dt(inicio)) == dt(fin_esperado)


# --------------------------------------------------------------------------
# Regla 10: fin de semana
# --------------------------------------------------------------------------

def test_el_json_no_define_sabado_ni_domingo(horarios):
    assert horarios.franja("TD", dt(f"{SABADO_INV} 12:00:00")) is None
    assert horarios.franja("TN", dt(f"{DOMINGO_INV} 12:00:00")) is None
    assert horarios.hueco(dt(f"{SABADO_INV} 12:00:00")) is None


@pytest.mark.parametrize("dia", [SABADO_INV, DOMINGO_INV])
@pytest.mark.parametrize("hora", ["18:00:00", "22:00:00", "03:00:00"])
def test_el_fin_de_semana_sin_turno_de_origen_queda_en_TD(horarios, dia, hora):
    """328 filas reales, todas de Termoformado y todas 'Periodo no laboral'."""
    assert horarios.clasificar("TERMOFORMADO", "T0", dt(f"{dia} {hora}")) == ("TD", Caso.FIN_DE_SEMANA)


def test_el_fin_de_semana_no_suma_horas_extra():
    """Decision del 2026-09-16: se etiquetan TD pero sin minutos extra."""
    assert sin_horas_extra(Caso.FIN_DE_SEMANA)
    assert sin_horas_extra(Caso.SIN_VIGENCIA)
    assert not sin_horas_extra(Caso.HUECO)
    assert not sin_horas_extra(Caso.CALCULADO)
    assert not sin_horas_extra(Caso.ORIGEN)


def test_el_fin_de_semana_con_turno_de_origen_lo_conserva(horarios):
    """3.645 de las 3.973 filas de fin de semana ya traen turno: no se tocan."""
    assert horarios.clasificar("TERMOFORMADO", "TN", dt(f"{SABADO_INV} 03:00:00")) == ("TN", Caso.ORIGEN)


# --------------------------------------------------------------------------
# La asimetría del hueco: clasifica hasta el inicio de TN, extras desde el fin de TD
# --------------------------------------------------------------------------

@pytest.mark.parametrize("dia,hora,fin_oficial", [
    (LUNES_INV, "20:00:00", "18:00:00"),
    (VIERNES_INV, "16:00:00", "15:00:00"),
])
def test_las_horas_extra_se_cuentan_contra_el_fin_de_TD(horarios, dia, hora, fin_oficial):
    inicio = dt(f"{dia} {hora}")
    assert horarios.resolver("TERMOFORMADO", "T0", inicio) == "TD"
    assert horarios.fin_de_turno("TERMOFORMADO", "TD", inicio) == dt(f"{dia} {fin_oficial}")


# --------------------------------------------------------------------------
# Validación del config
# --------------------------------------------------------------------------

def test_el_config_vigente_pasa_la_validacion():
    cfg = load_horarios()
    assert [v["id"] for v in cfg["vigencias"]] == ["2026-04-invierno", "2026-09-verano"]


def test_cada_vigencia_define_los_cinco_dias_habiles():
    habiles = {"lunes", "martes", "miercoles", "jueves", "viernes"}
    for v in load_horarios()["vigencias"]:
        for turno, por_dia in v["horarios"].items():
            assert set(por_dia) == habiles, f"{v['id']}.{turno}"
