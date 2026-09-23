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
    TNL,
    Caso,
    Horarios,
    es_feriado,
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
# Regla 8.bis: la cola del TN del viernes, ya en sábado (sábado no está en
# el JSON, así que `franja()` sola no alcanza para resolverla)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("inicio,franja_esperada", [
    (f"{SABADO_INV} 00:30:00", ("21:30", "08:30")),
    (f"{SABADO_INV} 02:00:00", ("21:30", "08:30")),
    (f"{SABADO_INV} 08:29:59", ("21:30", "08:30")),
    ("2026-09-12 00:30:00", ("20:00", "07:00")),   # sábado de verano
    ("2026-09-12 06:59:59", ("20:00", "07:00")),
])
def test_franja_vigente_resuelve_la_cola_del_TN_del_viernes(horarios, inicio, franja_esperada):
    """`franja()` sola da None (sábado no está en el JSON); `franja_vigente()` no."""
    assert horarios.franja("TN", dt(inicio)) is None
    obtenida = horarios.franja_vigente("TN", dt(inicio))
    assert obtenida is not None
    assert tuple(x.strftime("%H:%M") for x in obtenida) == franja_esperada


@pytest.mark.parametrize("inicio", [
    f"{SABADO_INV} 08:30:00",  # justo el borde: el TN del viernes ya terminó
    f"{SABADO_INV} 12:00:00",
])
def test_franja_vigente_no_se_extiende_mas_alla_del_fin_del_turno(horarios, inicio):
    """Pasado el fin del TN del viernes, el sábado sigue sin horario: no se inventa uno."""
    assert horarios.franja_vigente("TN", dt(inicio)) is None


def test_ventana_turno_del_TN_del_viernes_entrado_el_sabado(horarios):
    ventana = horarios.ventana_turno("TN", dt(f"{SABADO_INV} 02:00:00"))
    assert ventana == (dt(f"{VIERNES_INV} 21:30:00"), dt(f"{SABADO_INV} 08:30:00"))


@pytest.mark.parametrize("inicio,fin_esperado", [
    (f"{SABADO_INV} 00:30:00", f"{SABADO_INV} 08:30:00"),
    (f"{SABADO_INV} 02:00:00", f"{SABADO_INV} 08:30:00"),
    ("2026-09-12 03:00:00", "2026-09-12 07:00:00"),  # sábado de verano
])
def test_el_fin_de_TN_tambien_cae_bien_cuando_ya_es_sabado(horarios, inicio, fin_esperado):
    """Antes de `franja_vigente`, esto daba None: sin fin de turno, sin horas extra."""
    assert horarios.fin_de_turno("TERMOFORMADO", "TN", dt(inicio)) == dt(fin_esperado)


def test_el_TN_del_viernes_entrado_el_sabado_queda_dentro_de_su_propia_ventana(horarios):
    """Es la ventana que usa `_determinar_periodo_laboral` para decidir 'Sí'/'No'."""
    inicio = dt(f"{SABADO_INV} 02:00:00")
    ventana = horarios.ventana_turno("TN", inicio)
    assert ventana[0] <= inicio <= ventana[1]


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


# --------------------------------------------------------------------------
# Selección de vigencia por rango de fechas (incluye `vigente: false`)
# --------------------------------------------------------------------------
#
# `horarios.json` hoy no trae ninguna vigencia `vigente: false` -- las dos que
# existen están vigentes -- así que este bloque arma una config sintética
# mínima en vez de reusar `load_horarios()`, para probar el caso que sí va a
# ocurrir cuando se agregue una vigencia nueva y la anterior pase a `false`.

def _cfg_dos_vigencias():
    return {
        "turnos": {"TD": {"nombre": "Día"}},
        "areas": {"Termoformado": {"turnos_permitidos": ["TD"]}},
        "vigencias": [
            {
                "id": "vieja", "vigencia_desde": "2025-01-01", "vigente": False,
                "horarios": {"TD": {"lunes": {"inicio": "07:00", "fin": "16:00"}}},
            },
            {
                "id": "nueva", "vigencia_desde": "2026-01-01", "vigente": True,
                "horarios": {"TD": {"lunes": {"inicio": "08:00", "fin": "18:00"}}},
            },
        ],
    }


def test_una_vigencia_vigente_false_rige_las_fechas_de_su_propio_rango():
    """El flag `vigente` no decide qué vigencia aplica a una fecha: decide el
    rango completo entre su `vigencia_desde` y el de la siguiente."""
    horarios = Horarios(_cfg_dos_vigencias())
    vigencia = horarios.vigencia_para(dt("2025-06-02 12:00:00"))  # lunes, dentro del rango viejo
    assert vigencia.id == "vieja"
    assert horarios.franja("TD", dt("2025-06-02 09:00:00")) == (
        datetime.strptime("07:00", "%H:%M").time(),
        datetime.strptime("16:00", "%H:%M").time(),
    )


def test_una_fecha_posterior_usa_la_vigencia_vigente_aunque_haya_una_vieja():
    horarios = Horarios(_cfg_dos_vigencias())
    vigencia = horarios.vigencia_para(dt(f"{LUNES_INV} 12:00:00"))
    assert vigencia.id == "nueva"


def test_una_fecha_anterior_a_toda_vigencia_no_resuelve_ninguna():
    horarios = Horarios(_cfg_dos_vigencias())
    assert horarios.vigencia_para(dt("2024-01-01 12:00:00")) is None


# --------------------------------------------------------------------------
# es_feriado(): la única señal de feriado son los Comentarios
# --------------------------------------------------------------------------
#
# `horarios.json` no contempla feriados. Verificado sobre datos reales
# (Extrusión, agosto 2026): la palabra vive en `Comentarios`, no en
# `Desc_detencion`, y el turno puede venir ya declarado (TD/TT/TN, no T0).

@pytest.mark.parametrize("comentario", [
    "FERIADO", "feriado", "Feriado", "  FERIADO  ", "Feriado nacional",
])
def test_es_feriado_detecta_la_palabra_sin_importar_mayusculas_ni_espacios(comentario):
    assert es_feriado(comentario)


@pytest.mark.parametrize("comentario", [None, "", "   ", float("nan"), "OPERADOR", "Micro parada"])
def test_es_feriado_es_false_para_lo_que_no_marca_feriado(comentario):
    assert not es_feriado(comentario)


# --------------------------------------------------------------------------
# tramos(): fragmentación de un intervalo en un tramo por turno
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# fecha_inicio_turno(): la fecha del turno, no la del timestamp de la fila
# --------------------------------------------------------------------------
#
# horarios.json no define sábado ni domingo: un turno real nunca puede
# arrancar esos días. Un tramo TN cuyos datos ya caen en la madrugada del
# sábado (la cola del TN del viernes) le pertenece igual al viernes.

def test_fecha_inicio_turno_del_TN_que_sigue_en_sabado_es_el_viernes(horarios):
    momento = dt(f"{SABADO_INV} 01:37:00")
    assert horarios.fecha_inicio_turno("TN", momento) == dt(f"{VIERNES_INV} 00:00:00").date()


def test_fecha_inicio_turno_de_un_turno_que_no_cruza_medianoche_es_su_propio_dia(horarios):
    momento = dt(f"{LUNES_INV} 10:00:00")
    assert horarios.fecha_inicio_turno("TD", momento) == dt(f"{LUNES_INV} 00:00:00").date()


def test_fecha_inicio_turno_de_TNL_es_la_del_propio_tramo(horarios):
    """TNL no es un turno real: no hay "arranque" que perseguir más allá del sábado mismo."""
    momento = dt(f"{SABADO_INV} 10:00:00")
    assert horarios.fecha_inicio_turno(TNL, momento) == dt(f"{SABADO_INV} 00:00:00").date()


def test_tramos_ejemplo_base_TD_hueco_TN(horarios):
    """El caso de negocio original: TF5 16:00->23:37 TD se parte en TD/TNL/TN."""
    inicio, fin = dt("2026-09-22 16:00:00"), dt("2026-09-22 23:37:00")
    resultado = horarios.tramos("TERMOFORMADO", inicio, fin)
    assert [(t[0], t[1], t[2]) for t in resultado] == [
        (dt("2026-09-22 16:00:00"), dt("2026-09-22 18:00:00"), "TD"),
        (dt("2026-09-22 18:00:00"), dt("2026-09-22 23:00:00"), TNL),
        (dt("2026-09-22 23:00:00"), dt("2026-09-22 23:37:00"), "TN"),
    ]


def test_tramos_cruza_mas_de_dos_turnos_en_extrusion(horarios):
    """Extrusión tiene TD/TT/TN habilitados: un intervalo largo cruza los tres."""
    inicio = dt(f"{MARTES_INV} 08:00:00")
    fin = dt("2026-06-17 02:00:00")  # miércoles de madrugada
    resultado = horarios.tramos("EXTRUSIÓN", inicio, fin)

    assert [t[2] for t in resultado] == ["TD", "TT", "TN"]
    assert resultado[0][0] == inicio
    assert resultado[-1][1] == fin
    # Contiguos: sin huecos ni superposición entre tramos consecutivos.
    for anterior, siguiente in zip(resultado, resultado[1:]):
        assert anterior[1] == siguiente[0]


def test_tramos_no_genera_filas_de_duracion_cero_en_un_borde_exacto(horarios):
    """Un intervalo que arranca justo cuando termina TD no deja una fila TD vacía."""
    inicio = dt(f"{LUNES_INV} 18:00:00")  # fin exacto de TD en Termoformado
    fin = dt(f"{LUNES_INV} 19:00:00")
    resultado = horarios.tramos("TERMOFORMADO", inicio, fin)
    assert len(resultado) == 1
    assert resultado[0][:2] == (inicio, fin)
    assert resultado[0][2] == TNL


def test_tramos_lunes_de_madrugada_sin_TN_de_domingo_es_TNL(horarios):
    """Domingo no define TN: la madrugada del lunes (antes del TD) no es la
    cola de nada, aunque la franja propia de "TN lunes" (23:00->08:00) la
    cubra leída sin más contexto. Caso real: 2026-09-07 (lunes) traía filas
    TN a las 00:00 y 07:49-07:59 que quedaban con Fecha_Inicio_Turno=domingo
    -- un día que ni siquiera tiene TN definido.
    """
    inicio = dt(f"{LUNES_INV} 00:00:00")
    fin = dt(f"{LUNES_INV} 08:00:00")
    resultado = horarios.tramos("TERMOFORMADO", inicio, fin)
    assert len(resultado) == 1
    assert resultado[0][2] == TNL


def test_tramos_lunes_a_las_23_arranca_su_propio_TN(horarios):
    """La noche del lunes sí es "TN lunes" -- eso no cambia con el fix anterior."""
    inicio = dt(f"{LUNES_INV} 23:00:00")
    fin = dt(f"{LUNES_INV} 23:30:00")
    resultado = horarios.tramos("TERMOFORMADO", inicio, fin)
    assert resultado == [(inicio, fin, "TN", Caso.CALCULADO)]


def test_tramos_viernes_de_madrugada_usa_el_horario_del_jueves_no_el_propio(horarios):
    """Bug real: colgó el ETL en producción (2026-09-11, viernes, vigencia
    verano). El viernes tiene su propio TN (20:00->07:00, más temprano que el
    resto de la semana); antes de esa hora, la madrugada del viernes es la
    cola del TN del jueves (23:00->08:00) -- con los horarios de AYER, no los
    de hoy reinterpretados. Confundirlos arma una ventana que no corresponde
    a ningún turno real y que no avanza: `tramos()` quedaba girando en el
    mismo instante para siempre.
    """
    inicio = dt("2026-09-11 05:54:54")  # viernes, vigencia 2026-09-verano
    fin = dt("2026-09-11 08:00:00")     # fin del TN del jueves
    resultado = horarios.tramos("TERMOFORMADO", inicio, fin)

    assert resultado == [(inicio, fin, "TN", Caso.CALCULADO)]


def test_franja_vigente_de_la_madrugada_propia_usa_el_dia_anterior(horarios):
    momento = dt("2026-09-11 05:54:54")
    assert horarios.franja_vigente("TN", momento) == (
        datetime.strptime("23:00", "%H:%M").time(),
        datetime.strptime("08:00", "%H:%M").time(),
    )


def test_tramos_fin_de_semana_es_un_solo_tramo_no_uno_por_dia(horarios):
    """Sábado completo + domingo completo: un único tramo TNL, no fragmentado por día."""
    inicio = dt(f"{SABADO_INV} 08:30:00")   # justo tras el fin del TN del viernes
    fin = dt("2026-06-22 08:00:00")         # lunes siguiente, inicio del TD
    resultado = horarios.tramos("TERMOFORMADO", inicio, fin)
    assert len(resultado) == 1
    assert resultado[0][2] == TNL


def test_tramos_feriado_pisa_el_turno_ya_declarado_por_el_origen(horarios):
    """Un feriado en día de semana es TNL de punta a punta, sin fragmentar más."""
    inicio, fin = dt(f"{MARTES_INV} 10:00:00"), dt(f"{MARTES_INV} 12:00:00")
    resultado = horarios.tramos("EXTRUSIÓN", inicio, fin, comentario="Feriado")
    assert resultado == [(inicio, fin, TNL, Caso.FERIADO)]


def test_tramos_sin_area_conocida_no_fragmenta(horarios):
    inicio, fin = dt(f"{LUNES_INV} 08:00:00"), dt(f"{LUNES_INV} 20:00:00")
    resultado = horarios.tramos("LINEA NUEVA", inicio, fin)
    assert resultado == [(inicio, fin, None, Caso.SIN_AREA)]


def test_tramos_sin_vigencia_aplicable_no_fragmenta(horarios):
    inicio, fin = dt("2025-06-16 20:00:00"), dt("2025-06-17 02:00:00")
    resultado = horarios.tramos("TERMOFORMADO", inicio, fin)
    assert resultado == [(inicio, fin, None, Caso.SIN_VIGENCIA)]
