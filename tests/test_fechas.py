"""Corte del día operativo y conversión entre espacios de tiempo."""

from datetime import date, datetime, time, timedelta

import pytest

from typack_etl.extractor.pipelines import extractor_30m, extractor_mensual
from typack_etl.extractor.utils.date_utils import (
    a_api,
    a_local,
    a_planta,
    a_utc,
    api_en_verano,
    corte_utc,
    corte_utc_esperado,
    dia_operativo,
    inicio_dia_operativo,
    inicio_mes_operativo,
    mes_operativo,
    minutos_dia_operativo,
    month_name_es,
    now_local,
    now_planta,
    now_utc,
    parse_api_datetime,
    reloj_api,
    utc_a_local,
)


def dt(texto):
    return datetime.strptime(texto, "%Y-%m-%d %H:%M:%S")


def corte_txt(dia):
    """El corte que le corresponde a `dia`, como lo escribe la API.

    Las pruebas de ventana lo calculan por fecha en vez de leerlo de la
    configuracion: el corte se mueve dos veces al ano y antes estas pruebas
    pasaban o fallaban segun la estacion en que corriera la suite.
    """
    return corte_utc_esperado(dia).strftime("%H:%M:%S")


# --------------------------------------------------------------------------
# El corte
# --------------------------------------------------------------------------

@pytest.mark.parametrize("dia,esperado", [
    (date(2026, 1, 15), "11:00:00"),   # verano del pais
    (date(2026, 4, 4), "11:00:00"),    # ultimo dia de verano
    (date(2026, 4, 5), "12:00:00"),    # el pais atrasa: primer domingo de abril
    (date(2026, 6, 15), "12:00:00"),   # invierno
    (date(2026, 9, 5), "12:00:00"),
    (date(2026, 9, 6), "11:00:00"),    # el pais adelanta: primer domingo de septiembre
])
def test_el_corte_son_siempre_las_ocho_locales(dia, esperado):
    """El corte es el cambio de turno TD/TN, y ese es local, no UTC."""
    assert str(corte_utc_esperado(dia)) == esperado
    assert str(corte_utc(dia)) == esperado


def test_el_corte_sin_argumento_contesta_por_hoy():
    assert corte_utc() == corte_utc_esperado(now_local().date())


@pytest.mark.parametrize("dia,esperado", [
    (date(2026, 3, 15), "08:00:00"),   # relojes alineados
    (date(2026, 4, 5), "09:00:00"),    # el pais atraso y el proveedor no
    (date(2026, 6, 15), "08:00:00"),   # los dos en invierno
    (date(2026, 9, 5), "09:00:00"),    # el proveedor adelanto antes de tiempo
    (date(2026, 9, 6), "08:00:00"),    # se alinean
])
def test_el_corte_en_el_reloj_del_proveedor(dia, esperado):
    """horarios.json: TD arranca 08:00 y TN termina 08:00, hora local.

    En el reloj del proveedor ese instante cae a las 08:00 o a las 09:00 segun
    si los dos relojes estan alineados. Es el corrimiento que archivaba en
    septiembre segmentos que por turno pertenecian a agosto.
    """
    assert inicio_dia_operativo(dia).strftime("%H:%M:%S") == esperado
    assert a_local(inicio_dia_operativo(dia)).time() == time(8, 0)


def test_el_corte_cae_en_las_ocho_locales_todos_los_dias_de_tres_anos():
    """Invariante: el borde del dia operativo son las 08:00 locales, siempre."""
    dias = [date(2024, 1, 1) + timedelta(days=i) for i in range(365 * 3)]
    assert [d for d in dias if a_local(inicio_dia_operativo(d)).time() != time(8, 0)] == []


# --------------------------------------------------------------------------
# Los tres relojes
# --------------------------------------------------------------------------

@pytest.mark.parametrize("api,local", [
    ("2026-03-15 09:00:00", "2026-03-15 09:00:00"),  # verano: relojes alineados
    ("2026-04-04 08:00:00", "2026-04-04 08:00:00"),  # ultimo dia alineado
    ("2026-04-05 09:00:00", "2026-04-05 08:00:00"),  # el pais atraso y el proveedor no
    ("2026-05-09 09:00:00", "2026-05-09 08:00:00"),  # ultima jornada desfasada
    ("2026-05-10 08:00:00", "2026-05-10 08:00:00"),  # el proveedor atrasa: se alinean
    ("2026-07-15 08:00:00", "2026-07-15 08:00:00"),  # invierno: alineados
    ("2026-08-08 08:00:00", "2026-08-08 08:00:00"),
    ("2026-08-09 09:00:00", "2026-08-09 08:00:00"),  # el proveedor adelanta antes de tiempo
    ("2026-09-05 09:00:00", "2026-09-05 08:00:00"),
    ("2026-09-06 08:00:00", "2026-09-06 08:00:00"),  # el pais adelanta: se alinean
])
def test_a_local_corrige_solo_las_ventanas_de_desfase(api, local):
    """El reloj del proveedor usa las reglas de DST que Chile dejo en 2018.

    Observado en 340.055 filas de 2024-2026: los cortes de turno saltan de
    08:00 a 09:00 en el 1er domingo de abril y el 2o de agosto, y vuelven en el
    2o domingo de mayo y el 1o de septiembre.
    """
    assert a_local(dt(api)) == dt(local)


def test_el_desfase_dura_unas_nueve_semanas_al_ano():
    """Aplicar la correccion todo el invierno romperia los meses que estan bien."""
    dias = [date(2026, 1, 1) + timedelta(days=i) for i in range(365)]
    desfasados = [d for d in dias if a_local(datetime.combine(d, time(12, 0))).hour == 11]
    assert 55 <= len(desfasados) <= 70
    assert min(desfasados) == date(2026, 4, 5)
    assert max(desfasados) == date(2026, 9, 5)


def test_el_proveedor_cambia_de_hora_el_segundo_domingo():
    assert api_en_verano(dt("2026-08-09 00:00:00"))         # 2o domingo de agosto
    assert not api_en_verano(dt("2026-08-08 23:59:59"))
    assert not api_en_verano(dt("2026-05-10 00:00:00"))     # 2o domingo de mayo
    assert api_en_verano(dt("2026-05-09 23:59:59"))


@pytest.mark.parametrize("momento", [
    "2026-01-15 12:00:00", "2026-04-20 12:00:00", "2026-06-01 12:00:00",
    "2026-08-20 12:00:00", "2026-11-01 12:00:00",
])
def test_ida_y_vuelta_entre_espacios(momento):
    assert a_utc(a_planta(dt(momento))) == dt(momento)
    assert a_api(a_local(dt(momento))) == dt(momento)


def test_las_conversiones_son_reversibles_todo_el_ano():
    """Un desfase que dependa de la fecha no puede perder ni inventar horas."""
    momentos = [datetime(2024, 1, 1, 12, 0) + timedelta(days=i) for i in range(365 * 3)]
    assert [m for m in momentos if a_utc(a_planta(m)) != m] == []
    assert [m for m in momentos if a_api(a_local(m)) != m] == []


def test_utc_a_local_es_el_atajo_de_las_dos_conversiones():
    for i in range(0, 365, 7):
        momento = datetime(2026, 1, 1, 12, 0) + timedelta(days=i)
        assert utc_a_local(momento) == a_local(a_planta(momento))


def test_now_planta_va_el_desfase_de_hoy_detras_de_utc():
    desfase = reloj_api(now_planta()).utcoffset(None)
    assert abs((now_planta() - now_utc()) - desfase).total_seconds() < 2


# --------------------------------------------------------------------------
# Día y mes operativo
# --------------------------------------------------------------------------

@pytest.mark.parametrize("momento,mes_esperado", [
    ("2026-03-31 08:00:00", (2026, 3)),   # arranca el último día operativo de marzo
    ("2026-03-31 22:00:00", (2026, 3)),   # turno de noche
    ("2026-04-01 00:00:00", (2026, 3)),   # medianoche calendario: sigue siendo marzo
    ("2026-04-01 06:00:00", (2026, 3)),   # madrugada del turno que arrancó el 31
    ("2026-04-01 07:59:59", (2026, 3)),
    ("2026-04-01 08:00:00", (2026, 4)),   # el corte abre abril
    ("2026-04-01 12:00:00", (2026, 4)),
    ("2026-12-31 23:00:00", (2026, 12)),
    ("2027-01-01 03:00:00", (2026, 12)),  # cruce de año
    ("2027-01-01 09:00:00", (2027, 1)),
])
def test_mes_operativo(momento, mes_esperado):
    assert mes_operativo(dt(momento)) == mes_esperado


@pytest.mark.parametrize("momento,dia_esperado", [
    # Regresion del 2026-09-11: dentro de la ventana de desfase el reloj del
    # proveedor va +1 h, asi que su 08:34 son las 07:34 locales y el segmento
    # pertenece al turno de noche que arranco el 31-ago. Con el corte tomado de
    # una constante estos cinco segmentos se archivaron en septiembre.
    ("2026-09-01 08:31:48", date(2026, 8, 31)),
    ("2026-09-01 08:34:50", date(2026, 8, 31)),
    ("2026-09-01 08:44:17", date(2026, 8, 31)),
    ("2026-09-01 08:59:59", date(2026, 8, 31)),
    ("2026-09-01 09:00:00", date(2026, 9, 1)),   # 08:00 locales: recien abre septiembre
])
def test_el_desfase_no_corre_el_borde_del_mes_operativo(momento, dia_esperado):
    assert dia_operativo(dt(momento)) == dia_esperado
    assert mes_operativo(dt(momento)) == (dia_esperado.year, dia_esperado.month)


def test_el_dia_operativo_se_calcula_con_las_reglas_de_su_propia_fecha():
    """Reprocesar en otra estacion no puede mover un dato de mes.

    Es la diferencia entre derivar el corte de la fecha del dato y leerlo de
    una constante: la constante vale lo que valga el dia de la corrida.
    """
    en_desfase = dt("2026-09-01 08:34:50")     # proveedor +1 h
    alineado = dt("2026-06-01 08:34:50")       # relojes alineados
    assert dia_operativo(en_desfase) == date(2026, 8, 31)
    assert dia_operativo(alineado) == date(2026, 6, 1)


def test_el_turno_de_noche_cuenta_en_el_dia_en_que_empezo():
    assert dia_operativo(dt("2026-04-01 03:00:00")) == dia_operativo(dt("2026-03-31 22:00:00"))


def test_los_dias_operativos_son_contiguos_y_sin_solape():
    """El instante anterior al corte pertenece al dia anterior, todos los dias."""
    malos = []
    for i in range(365 * 3):
        dia = date(2024, 1, 1) + timedelta(days=i)
        arranca = inicio_dia_operativo(dia)
        if dia_operativo(arranca) != dia or dia_operativo(arranca - timedelta(seconds=1)) != dia - timedelta(days=1):
            malos.append(dia)
    assert malos == []


@pytest.mark.parametrize("dia,minutos", [
    (date(2026, 9, 4), 1440),
    (date(2026, 9, 5), 1380),   # esa noche el pais adelanta: el dia dura 23 h
    (date(2026, 9, 6), 1440),
    (date(2026, 4, 3), 1440),
    (date(2026, 4, 4), 1500),   # esa noche el pais atrasa: el dia dura 25 h
    (date(2026, 4, 5), 1440),
])
def test_el_dia_operativo_no_siempre_dura_1440_minutos(dia, minutos):
    """Es el denominador de la cobertura.

    Medido contra 1440 fijo, los dos cambios de hora del ano aparecian como
    una caida de cobertura de una hora en todas las lineas a la vez.
    """
    assert minutos_dia_operativo(dia) == minutos


def test_solo_dos_dias_al_ano_se_salen_de_las_24_horas():
    dias = [date(2024, 1, 1) + timedelta(days=i) for i in range(365 * 3)]
    raros = [d for d in dias if minutos_dia_operativo(d) != 1440]
    assert [(d.month, d.day) for d in raros] == [
        (4, 6), (9, 7), (4, 5), (9, 6), (4, 4), (9, 5),
    ]


def test_inicio_de_dia_y_de_mes_operativo():
    assert inicio_dia_operativo(date(2026, 4, 1)) == dt("2026-04-01 08:00:00")
    assert inicio_mes_operativo(2026, 4) == dt("2026-04-01 08:00:00")
    # Dentro de la ventana de desfase el mismo corte cae una hora despues en el
    # reloj del proveedor.
    assert inicio_mes_operativo(2026, 5) == dt("2026-05-01 09:00:00")


# --------------------------------------------------------------------------
# Ventana móvil
# --------------------------------------------------------------------------

def test_la_ventana_termina_en_el_corte_de_hoy_si_ya_paso(reloj):
    reloj(dt("2026-04-03 14:30:00"))
    inicio, fin = extractor_30m._auto_range(35)
    assert fin == dt(f"2026-04-03 {corte_txt(date(2026, 4, 3))}")
    assert inicio == dt(f"2026-02-27 {corte_txt(date(2026, 2, 27))}")


def test_si_el_corte_no_llego_la_ventana_termina_en_el_de_ayer(reloj):
    ahora = dt("2026-04-03 06:15:00")
    reloj(ahora)
    _, fin = extractor_30m._auto_range(35)
    assert fin == dt(f"2026-04-02 {corte_txt(date(2026, 4, 2))}")
    assert fin <= ahora, "nunca debe incluir un turno a medio andar"


@pytest.mark.parametrize("dias", [1, 7, 30, 35, 60])
def test_la_ventana_dura_exactamente_los_dias_pedidos(reloj, dias):
    reloj(dt("2026-04-03 14:30:00"))
    inicio, fin = extractor_30m._auto_range(dias)
    assert fin - inicio == timedelta(days=dias)
    assert inicio.time() == corte_utc_esperado(inicio.date())
    assert fin.time() == corte_utc_esperado(fin.date())


@pytest.mark.parametrize("dias", [1, 7, 30, 35, 60])
def test_los_dos_bordes_caen_en_el_corte_de_su_propio_dia(reloj, dias):
    """Ventana que cruza el cambio de hora del 5-abr.

    Medida en horas fijas, un extremo se saldria del turno: el corte pasa de
    11:00 a 12:00 UTC y la ventana tiene que seguirlo en cada borde por
    separado, aunque eso la haga durar una hora mas.
    """
    reloj(dt("2026-04-10 14:30:00"))
    inicio, fin = extractor_30m._auto_range(dias)
    assert inicio.time() == corte_utc_esperado(inicio.date())
    assert fin.time() == corte_utc_esperado(fin.date())
    assert a_local(a_planta(inicio)).time() == time(8, 0)
    assert a_local(a_planta(fin)).time() == time(8, 0)
    assert abs((fin - inicio) - timedelta(days=dias)) <= timedelta(hours=1)


def test_la_ventana_que_cruza_el_cambio_de_hora_dura_una_hora_mas(reloj):
    reloj(dt("2026-04-10 14:30:00"))
    inicio, fin = extractor_30m._auto_range(10)
    assert inicio == dt("2026-03-31 11:00:00")   # el pais todavia en verano
    assert fin == dt("2026-04-10 12:00:00")      # ya en invierno
    assert fin - inicio == timedelta(days=10, hours=1)


def test_los_overrides_se_interpretan_en_utc():
    """La cadena que se pasa es la que recibe la API, o sea UTC."""
    args = extractor_30m.parse_args(
        ["--start-date", "2026-04-01 12:00:00", "--end-date", "2026-05-01 12:00:00"]
    )
    inicio, fin, s, e = extractor_30m._resolve_window(args)
    assert (s, e) == ("2026-04-01 12:00:00", "2026-05-01 12:00:00")
    assert inicio == a_planta(dt("2026-04-01 12:00:00"))
    assert fin == a_planta(dt("2026-05-01 12:00:00"))


@pytest.mark.parametrize("flag", ["--start-date", "--end-date"])
def test_una_fecha_ilegible_aborta_en_vez_de_adivinar(flag):
    args = extractor_30m.parse_args([flag, "no es una fecha"])
    with pytest.raises(SystemExit):
        extractor_30m._resolve_window(args)


def test_un_rango_invertido_aborta():
    args = extractor_30m.parse_args(
        ["--start-date", "2026-05-01 12:00:00", "--end-date", "2026-04-01 12:00:00"]
    )
    with pytest.raises(SystemExit):
        extractor_30m._resolve_window(args)


# --------------------------------------------------------------------------
# Mes calendario del extractor mensual
# --------------------------------------------------------------------------

@pytest.mark.parametrize("hoy,desde,hasta", [
    ("2026-08-15 10:00:00", date(2026, 8, 1), date(2026, 9, 1)),
    ("2026-12-20 10:00:00", date(2026, 12, 1), date(2027, 1, 1)),
    ("2026-02-05 10:00:00", date(2026, 2, 1), date(2026, 3, 1)),
    # Mes que contiene el cambio de hora: no empieza y termina a la misma hora.
    ("2026-04-15 10:00:00", date(2026, 4, 1), date(2026, 5, 1)),
])
def test_el_mes_operativo_va_del_dia_1_al_dia_1(monkeypatch, hoy, desde, hasta):
    monkeypatch.setattr(extractor_mensual, "now_planta", lambda: dt(hoy))
    assert extractor_mensual._current_month_range_strings() == (
        f"{desde} {corte_txt(desde)}",
        f"{hasta} {corte_txt(hasta)}",
    )


def test_el_mes_de_abril_cruza_el_cambio_de_hora(monkeypatch):
    """Caso concreto del parametrizado anterior, para que se lea el efecto."""
    monkeypatch.setattr(extractor_mensual, "now_planta", lambda: dt("2026-04-15 10:00:00"))
    assert extractor_mensual._current_month_range_strings() == (
        "2026-04-01 11:00:00",   # el pais todavia en verano
        "2026-05-01 12:00:00",   # ya en invierno
    )


# --------------------------------------------------------------------------
# Parseo
# --------------------------------------------------------------------------

@pytest.mark.parametrize("texto,esperado", [
    ("2026-04-01 10:07:19", dt("2026-04-01 10:07:19")),
    ("2026-04-01 10:07", dt("2026-04-01 10:07:00")),
    ("2026-04-01", dt("2026-04-01 00:00:00")),
    ("", None),
    (None, None),
    ("basura", None),
])
def test_parse_api_datetime(texto, esperado):
    assert parse_api_datetime(texto) == esperado


def test_nombres_de_mes():
    assert month_name_es(4) == "abril"
    assert month_name_es(12) == "diciembre"
