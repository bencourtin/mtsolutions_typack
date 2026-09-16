"""Libro de auditoría, cobertura diaria y seguimiento de corridas."""

from datetime import datetime

import pytest

from conftest import FIXTURES, leer_csv, segmento
from typack_etl.audit import cdc
from typack_etl.audit.keys import record_key
from typack_etl.audit.ledger import (
    LEDGER_COLUMNS,
    LEDGER_FILENAME,
    append_cambios,
    claves_ausentes,
    cobertura_por_dia,
    guardar_ultimas_corridas,
    leer_ultimas_corridas,
    read_ledger,
    reasignaciones_recientes,
    registrar_cobertura,
)
from typack_etl.extractor.functions.io import read_csv_rows


def dt(texto):
    return datetime.strptime(texto, "%Y-%m-%d %H:%M:%S")


VENTANA = (dt("2026-03-01 00:00:00"), dt("2026-03-31 23:59:59"))
CTX = cdc.Contexto(run_id="r2", devid=1273, linea="TF_8",
                   detectado_en=dt("2026-03-25 07:00:00"), run_id_anterior="r1")


@pytest.fixture
def cambios():
    base = [segmento(f"2026-03-{d:02d} 08:00:00", f"2026-03-{d:02d} 09:00:00",
                     code_description="No asignado", code_t="No asignado")
            for d in range(1, 6)]
    nuevas = [dict(base[0], code_description="05.- Setup", code_t="Setup")] + base[1:]
    return base, cdc.comparar(base, nuevas, CTX, *VENTANA).cambios


# --------------------------------------------------------------------------
# Libro
# --------------------------------------------------------------------------

def test_el_libro_es_append_only(tmp_path, cambios):
    _, filas = cambios
    append_cambios(filas, tmp_path)
    append_cambios(filas, tmp_path)

    libro = read_ledger(tmp_path / LEDGER_FILENAME)
    assert len(libro) == 2 * len(filas)


def test_la_cabecera_se_escribe_una_sola_vez(tmp_path, cambios):
    _, filas = cambios
    append_cambios(filas, tmp_path)
    append_cambios(filas, tmp_path)

    texto = (tmp_path / LEDGER_FILENAME).read_text(encoding="utf-8-sig")
    assert texto.count("record_key;") == 1


def test_el_libro_lleva_las_17_columnas(tmp_path, cambios):
    _, filas = cambios
    append_cambios(filas, tmp_path)
    assert list(read_ledger(tmp_path / LEDGER_FILENAME)[0]) == list(LEDGER_COLUMNS)


def test_sin_cambios_no_crea_archivo(tmp_path):
    append_cambios([], tmp_path)
    assert read_ledger(tmp_path / LEDGER_FILENAME) == []


def test_la_vista_de_30_dias_filtra_por_antiguedad(tmp_path, cambios):
    _, filas = cambios
    append_cambios(filas, tmp_path)
    assert len(reasignaciones_recientes(tmp_path, 30)) == len(filas)
    assert reasignaciones_recientes(tmp_path, 5) == []


def test_el_libro_recuerda_las_claves_dadas_por_perdidas(tmp_path):
    base = [segmento(f"2026-03-{d:02d} 08:00:00", f"2026-03-{d:02d} 09:00:00") for d in range(1, 4)]
    r = cdc.comparar(base, base[1:], CTX, *VENTANA)
    append_cambios(r.cambios, tmp_path)
    assert claves_ausentes(tmp_path) == {record_key(1273, base[0])}


def test_una_clave_que_reaparece_deja_de_estar_ausente(tmp_path):
    base = [segmento(f"2026-03-{d:02d} 08:00:00", f"2026-03-{d:02d} 09:00:00") for d in range(1, 4)]
    perdida = cdc.comparar(base, base[1:], CTX, *VENTANA)
    append_cambios(perdida.cambios, tmp_path)

    vuelta = cdc.comparar(base[1:], base, CTX, *VENTANA, ausentes_conocidas=claves_ausentes(tmp_path))
    append_cambios(vuelta.cambios, tmp_path)
    assert claves_ausentes(tmp_path) == set()


# --------------------------------------------------------------------------
# Cobertura
# --------------------------------------------------------------------------

def test_el_turno_de_noche_cuenta_en_el_dia_en_que_empezo():
    noche = [segmento("2026-04-01 22:00:00", "2026-04-02 08:00:00", duration="600.0")]
    cob = cobertura_por_dia(noche, dt("2026-04-01 08:00:00"), dt("2026-04-03 08:00:00"))
    assert cob == {"2026-04-01": 600.0}


def test_la_madrugada_pertenece_al_dia_operativo_anterior():
    madrugada = [segmento("2026-04-02 03:00:00", "2026-04-02 05:00:00", duration="120.0")]
    cob = cobertura_por_dia(madrugada, dt("2026-04-01 08:00:00"), dt("2026-04-03 08:00:00"))
    assert cob == {"2026-04-01": 120.0}


def test_una_detencion_de_varios_dias_se_reparte(tmp_path):
    """Observado en producción: una detención de 46 h daba 193 % de cobertura."""
    larga = [segmento("2026-04-01 08:00:00", "2026-04-04 08:00:00", duration="4320.0")]
    cob = cobertura_por_dia(larga, dt("2026-04-01 08:00:00"), dt("2026-04-05 08:00:00"))

    assert sorted(cob) == ["2026-04-01", "2026-04-02", "2026-04-03"]
    assert all(v == pytest.approx(1440.0) for v in cob.values())
    assert sum(cob.values()) == pytest.approx(4320.0), "el total se conserva"


def test_el_reparto_es_proporcional_al_tiempo_en_cada_dia():
    """Empieza a mitad de un día operativo y termina a mitad del siguiente."""
    filas = [segmento("2026-04-01 20:00:00", "2026-04-02 20:00:00", duration="1440.0")]
    cob = cobertura_por_dia(filas, dt("2026-04-01 08:00:00"), dt("2026-04-03 08:00:00"))

    assert cob["2026-04-01"] == pytest.approx(720.0)   # 20:00 -> 08:00
    assert cob["2026-04-02"] == pytest.approx(720.0)   # 08:00 -> 20:00


def test_ninguna_cobertura_supera_el_dia_completo():
    filas = [
        segmento("2026-04-01 08:00:00", "2026-04-01 20:00:00", duration="720.0"),
        segmento("2026-04-01 20:00:00", "2026-04-05 08:00:00", duration="5040.0"),
    ]
    cob = cobertura_por_dia(filas, dt("2026-04-01 08:00:00"), dt("2026-04-06 08:00:00"))
    assert all(v <= 1440.0 + 1 for v in cob.values()), cob


def test_un_segmento_sin_fin_va_entero_al_dia_en_que_empezo():
    filas = [dict(segmento("2026-04-01 10:00:00", ""), duration="90.0")]
    cob = cobertura_por_dia(filas, dt("2026-04-01 08:00:00"), dt("2026-04-03 08:00:00"))
    assert cob == {"2026-04-01": 90.0}


def test_los_dias_incompletos_del_borde_se_excluyen():
    """Un día parcial siempre parecería incompleto y alertaría en cada corrida."""
    filas = [segmento("2026-04-01 10:00:00", "2026-04-01 11:00:00", duration="60.0")]
    cob = cobertura_por_dia(filas, dt("2026-04-01 10:00:00"), dt("2026-04-02 08:00:00"))
    assert cob == {}


def test_la_primera_corrida_no_alerta(tmp_path):
    caidas = registrar_cobertura("TF_8", 1273, {"2026-04-01": 1440.0}, tmp_path,
                                 "r1", dt("2026-04-03 07:00:00"))
    assert caidas == []


def test_detecta_y_cuantifica_una_caida_de_cobertura(tmp_path):
    registrar_cobertura("TF_8", 1273, {"2026-04-01": 1440.0}, tmp_path, "r1", dt("2026-04-03 07:00:00"))
    caidas = registrar_cobertura("TF_8", 1273, {"2026-04-01": 1260.0}, tmp_path, "r2", dt("2026-04-04 07:00:00"))

    assert len(caidas) == 1
    assert caidas[0]["delta"] == -180.0
    assert caidas[0]["minutos_antes"] == 1440.0


def test_no_alerta_dos_veces_por_lo_mismo(tmp_path):
    registrar_cobertura("TF_8", 1273, {"2026-04-01": 1440.0}, tmp_path, "r1", dt("2026-04-03 07:00:00"))
    registrar_cobertura("TF_8", 1273, {"2026-04-01": 1260.0}, tmp_path, "r2", dt("2026-04-04 07:00:00"))
    assert registrar_cobertura("TF_8", 1273, {"2026-04-01": 1260.0}, tmp_path, "r3",
                               dt("2026-04-05 07:00:00")) == []


def test_una_subida_de_cobertura_no_alerta(tmp_path):
    registrar_cobertura("TF_8", 1273, {"2026-04-01": 1200.0}, tmp_path, "r1", dt("2026-04-03 07:00:00"))
    assert registrar_cobertura("TF_8", 1273, {"2026-04-01": 1440.0}, tmp_path, "r2",
                               dt("2026-04-04 07:00:00")) == []


def test_no_alerta_por_diferencias_de_redondeo(tmp_path):
    registrar_cobertura("TF_8", 1273, {"2026-04-01": 1440.0}, tmp_path, "r1", dt("2026-04-03 07:00:00"))
    assert registrar_cobertura("TF_8", 1273, {"2026-04-01": 1439.5}, tmp_path, "r2",
                               dt("2026-04-04 07:00:00")) == []


def test_un_dia_que_reporta_su_jornada_completa_no_es_una_caida(tmp_path):
    """El 5-sep-2026 dura 23 h: esa noche el pais adelanta la hora.

    La corrida anterior habia anotado 1440 porque el borde del dia se calculaba
    sumando 24 h fijas y contaba dos veces la hora del cambio. Bajar a los 1380
    reales es la correccion, no una perdida de datos.
    """
    registrar_cobertura("TF_8", 1273, {"2026-09-05": 1440.0}, tmp_path, "r1", dt("2026-09-07 07:00:00"))
    caidas = registrar_cobertura("TF_8", 1273, {"2026-09-05": 1380.0}, tmp_path, "r2", dt("2026-09-08 07:00:00"))
    assert caidas == []


def test_un_dia_corto_que_pierde_minutos_de_verdad_si_alerta(tmp_path):
    """La excepcion anterior no puede tapar una caida real en el mismo dia."""
    registrar_cobertura("TF_8", 1273, {"2026-09-05": 1380.0}, tmp_path, "r1", dt("2026-09-07 07:00:00"))
    caidas = registrar_cobertura("TF_8", 1273, {"2026-09-05": 1200.0}, tmp_path, "r2", dt("2026-09-08 07:00:00"))
    assert len(caidas) == 1
    assert caidas[0]["delta"] == -180.0


def test_la_cobertura_del_dia_corto_es_100_por_ciento(tmp_path):
    registrar_cobertura("TF_8", 1273, {"2026-09-05": 1380.0}, tmp_path, "r1", dt("2026-09-07 07:00:00"))
    fila = read_csv_rows(tmp_path / "cobertura_diaria.csv")[0]
    assert float(str(fila["pct_cobertura"]).replace(",", ".")) == 100.0


def test_la_cobertura_se_acumula_por_linea_sin_pisarse(tmp_path):
    registrar_cobertura("TF_8", 1273, {"2026-04-01": 1440.0}, tmp_path, "r1", dt("2026-04-03 07:00:00"))
    registrar_cobertura("TF_9", 1274, {"2026-04-01": 1200.0}, tmp_path, "r1", dt("2026-04-03 07:00:00"))
    filas = read_csv_rows(tmp_path / "cobertura_diaria.csv")
    assert {f["linea"] for f in filas} == {"TF_8", "TF_9"}


def test_detecta_la_perdida_real_de_44_horas_de_tf5(tmp_path):
    """La caída observada entre las extracciones del 10-mar y el 24-mar."""
    a = FIXTURES / "processed" / "sabana_2026-02-08_00_00_2026-03-09_23_59_59_TF_5_1270.csv"
    b = FIXTURES / "processed" / "sabana_2026-02-22_00_00_2026-03-23_23_59_59_TF_5_1270.csv"
    if not (a.exists() and b.exists()):
        pytest.skip("faltan los fixtures de TF_5")

    ventana = (dt("2026-02-22 00:00:00"), dt("2026-03-09 23:59:59"))
    antes = cobertura_por_dia(read_csv_rows(a), *ventana)
    despues = cobertura_por_dia(read_csv_rows(b), *ventana)

    registrar_cobertura("TF_5", 1270, antes, tmp_path, "r1", dt("2026-03-10 17:54:00"))
    caidas = registrar_cobertura("TF_5", 1270, despues, tmp_path, "r2", dt("2026-03-24 10:38:00"))

    assert len(caidas) == 14
    assert sum(c["delta"] for c in caidas) == pytest.approx(-2640.0, abs=1)


# --------------------------------------------------------------------------
# run_id
# --------------------------------------------------------------------------

def test_guarda_y_recupera_la_ultima_corrida_por_linea(tmp_path):
    guardar_ultimas_corridas({"TF_8": "run-1", "TF_9": "run-2"}, tmp_path)
    assert leer_ultimas_corridas(tmp_path) == {"TF_8": "run-1", "TF_9": "run-2"}


def test_sin_archivo_devuelve_vacio(tmp_path):
    assert leer_ultimas_corridas(tmp_path) == {}


def test_un_json_corrupto_no_rompe_la_corrida(tmp_path):
    (tmp_path / "ultimas_corridas.json").write_text("{esto no es json", encoding="utf-8")
    assert leer_ultimas_corridas(tmp_path) == {}
