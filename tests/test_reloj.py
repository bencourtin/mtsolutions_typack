"""Detector del reloj del proveedor: `duration` contra el tiempo real.

Es la alarma que faltaba para el pendiente §3.3 de CONTEXTO_CAMBIO_DE_HORA.md:
el modelo temporal se sostiene en una inferencia sobre los datos, y hasta ahora
nada la contrastaba dentro de la corrida.
"""

import pytest

from typack_etl.audit import reloj
from typack_etl.extractor.pipelines.extractor_30m import _estado_linea

from conftest import segmento


def fila(start, end, duration):
    return segmento(start, end, duration=str(duration))


# --------------------------------------------------------------------------
# Lo normal: no dispara
# --------------------------------------------------------------------------

@pytest.mark.parametrize("start,end,duration", [
    ("2026-06-15 10:00:00", "2026-06-15 11:00:00", 60.0),
    ("2026-06-15 10:00:00", "2026-06-15 10:00:00", 0.0),      # micro parada
    ("2026-06-15 22:00:00", "2026-06-16 08:00:00", 600.0),    # turno de noche
    ("2026-09-01 08:00:00", "2026-09-01 09:30:30", 90.5),     # dentro del desfase
    ("2026-03-15 10:00:00", "2026-03-23 12:00:00", 11640.0),  # ocho dias
])
def test_un_segmento_coherente_no_dispara(start, end, duration):
    assert reloj.desajustes([fila(start, end, duration)]) == []


@pytest.mark.parametrize("delta", [0.0, 0.5, -0.9])
def test_el_ruido_de_redondeo_no_dispara(delta):
    """`duration` viene con decimales; la alarma es para desfases de una hora."""
    assert reloj.desajustes([fila("2026-06-15 10:00:00", "2026-06-15 11:00:00", 60.0 + delta)]) == []


# --------------------------------------------------------------------------
# Las noches en que el proveedor salta: tampoco dispara
# --------------------------------------------------------------------------

@pytest.mark.parametrize("start,end,duration,reloj_pared", [
    # 2o domingo de agosto: el proveedor adelanta, el reloj marca una hora de mas
    ("2025-08-09 22:00:00", "2025-08-10 09:00:00", 600.0, 660),
    ("2026-08-08 22:00:00", "2026-08-09 09:00:00", 600.0, 660),
    # 2o domingo de mayo: el proveedor atrasa, el reloj marca una hora de menos
    ("2025-05-10 23:00:00", "2025-05-11 08:00:00", 600.0, 540),
    ("2026-05-09 23:00:00", "2026-05-10 08:00:00", 600.0, 540),
])
def test_la_noche_del_cambio_de_hora_cuadra(start, end, duration, reloj_pared):
    """Casos reales de produccion: `duration` != end-start y aun asi esta bien.

    Es la prueba de que el detector mide contra el modelo y no contra el reloj
    de pared, que es lo que lo hace capaz de detectar un cambio de reglas.
    """
    from datetime import datetime
    a = datetime.strptime(start, "%Y-%m-%d %H:%M:%S")
    b = datetime.strptime(end, "%Y-%m-%d %H:%M:%S")
    assert (b - a).total_seconds() / 60 == reloj_pared, "el caso deberia diferir del reloj"
    assert reloj.desajustes([fila(start, end, duration)]) == []


# --------------------------------------------------------------------------
# Lo que si tiene que disparar
# --------------------------------------------------------------------------

def test_dispara_si_el_proveedor_salta_donde_no_lo_esperamos():
    """Si adoptaran las reglas vigentes de Chile, saltarian el 1er dom de septiembre."""
    fuera = reloj.desajustes([fila("2026-09-05 22:00:00", "2026-09-06 08:00:00", 540.0)], "TF_8")
    assert len(fuera) == 1
    assert fuera[0].delta == pytest.approx(-60.0)
    assert fuera[0].linea == "TF_8"
    assert "TF_8" in str(fuera[0])


def test_dispara_si_deja_de_saltar_cuando_lo_esperamos():
    """El caso inverso: el proveedor arregla su tabla y ya no adelanta en agosto."""
    fuera = reloj.desajustes([fila("2025-08-09 22:00:00", "2025-08-10 09:00:00", 660.0)])
    assert len(fuera) == 1
    assert fuera[0].delta == pytest.approx(60.0)


def test_el_diagnostico_distingue_un_cambio_de_hora_de_otra_cosa():
    de_una_hora = reloj.desajustes([fila("2026-09-05 22:00:00", "2026-09-06 08:00:00", 540.0)])
    assert "tabla de zonas horarias" in reloj.diagnostico(de_una_hora)

    otra_cosa = reloj.desajustes([fila("2026-06-15 10:00:00", "2026-06-15 11:00:00", 45.0)])
    assert "no es un cambio de hora" in reloj.diagnostico(otra_cosa)

    assert reloj.diagnostico([]) == ""


# --------------------------------------------------------------------------
# Filas que no son comparables
# --------------------------------------------------------------------------

@pytest.mark.parametrize("start,end,duration", [
    ("2026-06-15 10:00:00", "", "60.0"),                       # sin fin
    ("2026-06-15 10:00:00", "2026-06-15 09:00:00", "60.0"),    # fin antes del inicio
    ("", "2026-06-15 11:00:00", "60.0"),                       # sin inicio
    ("2026-06-15 10:00:00", "2026-06-15 11:00:00", ""),        # sin duracion
    ("2026-06-15 10:00:00", "2026-06-15 11:00:00", "basura"),
])
def test_una_fila_incomparable_se_saltea_en_vez_de_alertar(start, end, duration):
    """Una alarma sólo sirve si es silenciosa: el ruido la vuelve inútil."""
    assert reloj.desajustes([segmento(start, end, duration=duration)]) == []


def test_acepta_la_coma_decimal_del_csv():
    assert reloj.desajustes([fila("2026-06-15 10:00:00", "2026-06-15 11:00:00", "60,0")]) == []
    assert len(reloj.desajustes([fila("2026-06-15 10:00:00", "2026-06-15 11:00:00", "45,0")])) == 1


# --------------------------------------------------------------------------
# Integracion con el estado de la linea
# --------------------------------------------------------------------------

def test_el_desajuste_marca_la_linea():
    filas = [fila("2026-06-15 10:00:00", "2026-06-15 11:00:00", 60.0)]
    fuera = reloj.desajustes([fila("2026-09-05 22:00:00", "2026-09-06 08:00:00", 540.0)])
    assert _estado_linea(filas, [], {"2026-06-15": 1440.0}, fuera) == "RELOJ_INESPERADO"
    assert _estado_linea(filas, [], {"2026-06-15": 1440.0}, []) == "OK"


def test_el_reloj_pesa_mas_que_la_cobertura_pero_menos_que_no_haber_datos():
    filas = [fila("2026-06-15 10:00:00", "2026-06-15 11:00:00", 60.0)]
    fuera = reloj.desajustes([fila("2026-09-05 22:00:00", "2026-09-06 08:00:00", 540.0)])
    caidas = [{"linea": "TF_8", "fecha": "2026-06-15", "delta": -60.0}]
    assert _estado_linea(filas, caidas, {}, fuera) == "RELOJ_INESPERADO"
    assert _estado_linea([], caidas, {}, fuera) == "SIN_DATOS"


def test_el_estado_sigue_andando_sin_el_argumento_nuevo():
    """La firma vieja tiene que seguir funcionando: la usan otros tests y scripts."""
    filas = [fila("2026-06-15 10:00:00", "2026-06-15 11:00:00", 60.0)]
    assert _estado_linea(filas, [], {"2026-06-15": 1440.0}) == "OK"


def test_el_estado_nuevo_esta_en_los_avisos_del_orquestador():
    from scripts.run_daily_etl import ESTADOS_DE_AVISO
    assert "RELOJ_INESPERADO" in ESTADOS_DE_AVISO
