"""Particionado por mes operativo y reemplazo por rango (fase 1)."""

from datetime import datetime

import pytest

from conftest import PAR_IDENTICO, hay_fixture, leer_csv, segmento
from typack_etl.extractor.functions.io import (
    month_partitions,
    partition_by_month,
    read_csv_rows,
    solapa_ventana,
    write_csv,
    write_window_csv,
)


def dt(texto):
    return datetime.strptime(texto, "%Y-%m-%d %H:%M:%S")


def dia(d, mes=3, hora="08:00:00", **campos):
    fin = f"2026-{mes:02d}-{d:02d} 09:00:00"
    return segmento(f"2026-{mes:02d}-{d:02d} {hora}", fin, **campos)


# --------------------------------------------------------------------------
# Qué particiones toca una ventana
# --------------------------------------------------------------------------

@pytest.mark.parametrize("inicio,fin,esperado", [
    ("2026-03-17 08:00:00", "2026-04-19 08:00:00", [(2026, 3), (2026, 4)]),
    ("2025-12-20 08:00:00", "2026-01-24 08:00:00", [(2025, 12), (2026, 1)]),
    ("2026-04-02 08:00:00", "2026-04-09 08:00:00", [(2026, 4)]),
    ("2026-01-15 08:00:00", "2026-04-15 08:00:00", [(2026, 1), (2026, 2), (2026, 3), (2026, 4)]),
])
def test_month_partitions(inicio, fin, esperado):
    assert month_partitions(dt(inicio), dt(fin)) == esperado


def test_una_ventana_que_termina_en_el_corte_del_dia_1_no_arrastra_el_mes_siguiente():
    assert month_partitions(dt("2026-03-20 08:00:00"), dt("2026-04-01 08:00:00")) == [(2026, 3)]


def test_agrupa_por_mes_operativo_y_no_por_calendario():
    filas = [
        segmento("2026-03-31 22:00:00", "2026-04-01 06:00:00"),   # marzo
        segmento("2026-04-01 06:00:00", "2026-04-01 08:00:00"),   # marzo
        segmento("2026-04-01 08:00:00", "2026-04-01 18:00:00"),   # abril
    ]
    grupos, sin_fecha = partition_by_month(filas)
    assert sorted(grupos) == [(2026, 3), (2026, 4)]
    assert len(grupos[(2026, 3)]) == 2
    assert len(grupos[(2026, 4)]) == 1
    assert sin_fecha == []


def test_las_filas_sin_fecha_se_separan_en_vez_de_caer_en_un_mes_cualquiera():
    _, sin_fecha = partition_by_month([{"start": "basura"}])
    assert len(sin_fecha) == 1


# --------------------------------------------------------------------------
# Qué cae dentro de la ventana
# --------------------------------------------------------------------------

@pytest.mark.parametrize("inicio,fin,dentro", [
    ("2026-03-10 08:00:00", "2026-03-10 09:00:00", True),    # íntegro dentro
    ("2026-02-28 22:00:00", "2026-03-01 06:00:00", True),    # cruza el inicio
    ("2026-02-27 08:00:00", "2026-02-27 18:00:00", False),   # íntegro antes
    ("2026-02-28 20:00:00", "2026-03-01 00:00:00", False),   # termina justo EN el inicio: fuera
    ("2026-04-05 08:00:00", "2026-04-05 09:00:00", False),   # después del fin
])
def test_solapa_ventana(inicio, fin, dentro):
    ventana = (dt("2026-03-01 00:00:00"), dt("2026-03-31 23:59:59"))
    assert solapa_ventana(segmento(inicio, fin), *ventana) is dentro


def test_un_segmento_que_termina_justo_donde_empieza_la_ventana_queda_fuera():
    """Está íntegramente antes: la API no lo reporta y no hay que borrarlo."""
    ventana = (dt("2026-03-01 00:00:00"), dt("2026-03-31 23:59:59"))
    justo_antes = segmento("2026-02-28 20:00:00", "2026-03-01 00:00:00")
    assert solapa_ventana(justo_antes, *ventana) is False


def test_un_segmento_de_duracion_cero_en_el_borde_entra():
    """Las micro-paradas de 0 min existen y no deben perderse."""
    ventana = (dt("2026-03-01 00:00:00"), dt("2026-03-31 23:59:59"))
    cero = segmento("2026-03-01 00:00:00", "2026-03-01 00:00:00")
    assert solapa_ventana(cero, *ventana) is True


# --------------------------------------------------------------------------
# Reemplazo por rango: lo que evita destruir el histórico
# --------------------------------------------------------------------------

@pytest.fixture
def marzo_completo(tmp_path):
    """Un archivo de marzo con los 31 días, como lo dejó el extractor mensual."""
    destino = tmp_path / "produccion_2026_marzo_TF_8.csv"
    write_csv([dia(d) for d in range(1, 32)], destino)
    return destino


def test_conserva_lo_anterior_a_la_ventana(marzo_completo):
    ventana = (dt("2026-03-17 00:00:00"), dt("2026-04-19 23:59:59"))
    nuevas = [dia(d, code_description="05.- Setup") for d in range(17, 32)]

    write_window_csv(nuevas, marzo_completo, *ventana)

    filas = {r["start"]: r for r in read_csv_rows(marzo_completo)}
    assert len(filas) == 31
    assert filas["2026-03-01 08:00:00"]["code_description"] == ""
    assert filas["2026-03-17 08:00:00"]["code_description"] == "05.- Setup"


def test_lo_que_la_api_dejo_de_reportar_desaparece(marzo_completo):
    ventana = (dt("2026-03-17 00:00:00"), dt("2026-04-19 23:59:59"))
    nuevas = [dia(d) for d in range(17, 32) if d != 20]

    stats = write_window_csv(nuevas, marzo_completo, *ventana)

    filas = {r["start"] for r in read_csv_rows(marzo_completo)}
    assert "2026-03-20 08:00:00" not in filas
    assert len(filas) == 30
    assert stats["conservadas"] == 16


def test_queda_ordenado_cronologicamente(marzo_completo):
    ventana = (dt("2026-03-17 00:00:00"), dt("2026-04-19 23:59:59"))
    write_window_csv([dia(d) for d in range(17, 32)], marzo_completo, *ventana)
    starts = [r["start"] for r in read_csv_rows(marzo_completo)]
    assert starts == sorted(starts)


def test_reextraer_lo_mismo_no_duplica_nada(tmp_path):
    """La API devuelve segmentos anteriores al inicio de la ventana."""
    hay_fixture(*PAR_IDENTICO)
    import csv as _csv
    with PAR_IDENTICO[0].open(encoding="utf-8") as f:
        filas = list(_csv.DictReader(f))

    destino = tmp_path / "particion.csv"
    write_csv(filas, destino)
    ventana = (dt("2026-04-01 12:00:00"), dt("2026-05-01 12:00:00"))
    for _ in range(5):
        write_window_csv(filas, destino, *ventana)

    resultado = read_csv_rows(destino)
    claves = [(r["start"], r["end"]) for r in resultado]
    assert len(resultado) == len(filas)
    assert len(set(claves)) == len(claves), "hay filas duplicadas"


def test_una_fila_sin_fecha_se_conserva(tmp_path):
    destino = tmp_path / "particion.csv"
    write_csv([{"start": "", "end": "", "production": "5"}], destino)
    stats = write_window_csv([], destino, dt("2026-03-01 00:00:00"), dt("2026-03-31 23:59:59"))
    assert stats["sin_fecha"] == 1
    assert len(read_csv_rows(destino)) == 1
