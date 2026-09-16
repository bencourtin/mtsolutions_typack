"""Esquema canónico, escritura atómica y bitácoras (fase 0)."""

import csv

import pytest

from conftest import leer_csv, segmento
from typack_etl.extractor.functions.io import (
    COLUMNAS_TRAZA,
    SABANA_COLUMNS,
    atomic_open,
    fieldnames_for,
    format_value,
    identidad,
    infer_rows,
    nuevo_run_id,
    read_csv_rows,
    run_stamp,
    safe_filename,
    write_csv,
    write_plan_log,
    write_results_log,
)


# --------------------------------------------------------------------------
# Esquema canónico
# --------------------------------------------------------------------------

def test_el_esquema_tiene_21_columnas():
    assert len(SABANA_COLUMNS) == 21
    assert SABANA_COLUMNS[0] == "start"
    assert "order" in SABANA_COLUMNS


def test_la_cabecera_no_depende_de_las_claves_que_traiga_la_api():
    """La API omite las claves nulas; la cabecera debe ser siempre la misma."""
    con_order = {c: "x" for c in SABANA_COLUMNS}
    sin_order = {c: "x" for c in SABANA_COLUMNS if c != "order"}
    esperada = list(SABANA_COLUMNS) + list(COLUMNAS_TRAZA)
    assert fieldnames_for([sin_order]) == fieldnames_for([con_order]) == esperada


def test_las_columnas_de_traza_van_despues_de_las_de_la_api():
    cabecera = fieldnames_for([{c: "x" for c in SABANA_COLUMNS}])
    assert cabecera[:len(SABANA_COLUMNS)] == list(SABANA_COLUMNS)
    assert cabecera[len(SABANA_COLUMNS):] == list(COLUMNAS_TRAZA)


def test_una_columna_desconocida_se_anexa_en_vez_de_perderse():
    fila = dict({c: "x" for c in SABANA_COLUMNS}, campo_nuevo="z")
    assert fieldnames_for([fila])[-1] == "campo_nuevo"


def test_las_columnas_ausentes_se_escriben_vacias(tmp_path):
    destino = tmp_path / "sin_order.csv"
    write_csv([{c: "x" for c in SABANA_COLUMNS if c != "order"}], destino)

    with destino.open(encoding="utf-8-sig") as f:
        cabecera = next(csv.reader(f, delimiter=";"))
    assert cabecera[:len(SABANA_COLUMNS)] == list(SABANA_COLUMNS)
    assert leer_csv(destino)[0]["order"] == ""


def test_sin_filas_igual_escribe_la_cabecera(tmp_path):
    """Antes generaba un archivo vacío y pandas fallaba con EmptyDataError."""
    destino = tmp_path / "vacio.csv"
    assert write_csv([], destino) == 0

    pd = pytest.importorskip("pandas")
    df = pd.read_csv(destino, sep=";", encoding="utf-8")
    assert len(df) == 0
    assert list(df.columns) == list(SABANA_COLUMNS) + list(COLUMNAS_TRAZA)


# --------------------------------------------------------------------------
# Escritura atómica
# --------------------------------------------------------------------------

def test_un_fallo_a_media_escritura_no_daña_el_archivo_anterior(tmp_path):
    destino = tmp_path / "datos.csv"
    write_csv([segmento("2026-03-01 08:00:00", "2026-03-01 09:00:00")], destino)
    original = destino.read_bytes()

    with pytest.raises(RuntimeError):
        with atomic_open(destino) as f:
            f.write("basura parcial")
            raise RuntimeError("fallo simulado")

    assert destino.read_bytes() == original
    assert not (tmp_path / "datos.csv.tmp").exists()


def test_crea_los_directorios_padres(tmp_path):
    destino = tmp_path / "a" / "b" / "c.csv"
    assert write_csv([segmento("2026-03-01 08:00:00", "2026-03-01 09:00:00")], destino) == 1
    assert destino.exists()


# --------------------------------------------------------------------------
# Formato de valores
# --------------------------------------------------------------------------

@pytest.mark.parametrize("entrada,esperado", [
    (4.3666, "4,3666"),
    (50, "50"),
    ("12.34", "12,34"),
    ("BAND. CARNE", "BAND. CARNE"),   # texto con punto: no es un decimal
    ("2026-04-01 10:07:19", "2026-04-01 10:07:19"),
    (None, ""),
    ("4,3666", "4,3666"),            # idempotente sobre lo ya formateado
])
def test_format_value(entrada, esperado):
    assert format_value(entrada) == esperado


@pytest.mark.parametrize("entrada,esperado", [
    ([{"a": 1}, "ruido"], [{"a": 1}]),
    ({"data": [{"a": 1}]}, [{"a": 1}]),
    ({"results": [{"a": 1}]}, [{"a": 1}]),
    ({"a": 1}, [{"a": 1}]),
    (None, []),
])
def test_infer_rows(entrada, esperado):
    assert infer_rows(entrada) == esperado


def test_safe_filename():
    assert safe_filename("2026-04-01 12:00:00") == "2026-04-01_12_00_00"


def test_identidad_normaliza_los_dos_origenes():
    """Un lado viene de la API (float) y el otro de un CSV (texto)."""
    api = segmento("2026-03-01 08:00:00", "2026-03-01 09:00:00")
    csv_row = {k: format_value(v) for k, v in api.items()}
    assert identidad(api) == identidad(csv_row)


# --------------------------------------------------------------------------
# Bitácoras
# --------------------------------------------------------------------------

def test_plan_y_resultados_comparten_la_marca_de_la_corrida(tmp_path):
    stamp = run_stamp()
    item = {"line": "TF_8", "devid": 1273, "start": "s", "end": "e", "output_path": "x.csv"}
    plan = write_plan_log([item], tmp_path, stamp)
    res = write_results_log([dict(item, rows=0, success=False, error="")], tmp_path, stamp)
    assert plan.name == f"plan_{stamp}.csv"
    assert res.name == f"results_{stamp}.csv"


def test_un_error_con_punto_y_coma_no_descuadra_las_columnas(tmp_path):
    stamp = run_stamp()
    res = write_results_log(
        [{"line": "TF_8", "devid": 1273, "start": "s", "end": "e", "rows": 0,
          "success": False, "error": "HTTP 500; timeout de red"}],
        tmp_path, stamp,
    )
    fila = leer_csv(res)[0]
    assert fila["error"] == "HTTP 500; timeout de red"
    assert fila["success"] == "0"


def test_read_csv_rows_tolera_archivo_inexistente(tmp_path):
    assert read_csv_rows(tmp_path / "no_existe.csv") == []


def test_los_run_id_son_unicos():
    assert nuevo_run_id() != nuevo_run_id()
