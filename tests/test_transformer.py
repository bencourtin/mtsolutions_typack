"""Reglas de negocio: hora local, mes operativo y ausencia del split de medianoche."""

import csv
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from conftest import PAR_IDENTICO, hay_fixture, segmento
from typack_etl.extractor.functions.io import write_csv
from typack_etl.transformer.transformer import DataTransformer

FUENTE = Path("src/typack_etl/transformer/transformer.py")


@pytest.fixture
def transformer(tmp_path):
    """Transformador con las TRES rutas en temporal.

    `base_path` por sí solo no aísla nada: las salidas seguirían saliendo de
    PATHS, o sea de producción.
    """
    return DataTransformer(
        base_path=str(tmp_path / "raw"),
        processed_path=str(tmp_path / "processed"),
        consolidated_path=str(tmp_path / "consolidated"),
    )


def escribir(transformer, filas, mes="03", nombre="marzo"):
    destino = Path(transformer.base_path) / "2026" / mes / f"produccion_2026_{nombre}_TF_8.csv"
    write_csv(filas, destino)
    return destino


# --------------------------------------------------------------------------
# Aislamiento
# --------------------------------------------------------------------------

def test_las_tres_rutas_se_pueden_aislar(tmp_path):
    t = DataTransformer(
        base_path=str(tmp_path / "raw"),
        processed_path=str(tmp_path / "processed"),
        consolidated_path=str(tmp_path / "consolidated"),
    )
    assert str(tmp_path) in t.base_path
    assert str(tmp_path) in t.processed_path
    assert str(tmp_path) in t.consolidated_path


# --------------------------------------------------------------------------
# Sin split de medianoche
# --------------------------------------------------------------------------

def test_el_metodo_del_split_ya_no_existe():
    assert not hasattr(DataTransformer, "_split_midnight_crossings")
    assert "Registro_Dividido" not in FUENTE.read_text(encoding="utf-8")


def test_un_registro_que_cruza_medianoche_sigue_siendo_uno(transformer):
    escribir(transformer, [segmento("2026-03-10 22:00:00", "2026-03-11 04:33:00",
                                    production="4300", duration="393.0", turn="TN")])
    df = transformer.transform_data(transformer.read_line_data("TF_8", "2026", "03"))

    assert len(df) == 1
    assert int(df.iloc[0]["Q_prod_total"]) == 4300
    assert str(df.iloc[0]["Fecha_Hora_Termino"]) == "2026-03-11 04:33:00"
    assert "Registro_Dividido" not in df.columns


def test_un_registro_de_varios_dias_conserva_su_duracion(transformer):
    """El split de dos tramos descartaba los días del medio."""
    escribir(transformer, [segmento("2026-03-10 21:00:00", "2026-03-20 10:14:58",
                                    duration="13755.0", production="0", turn="TN")])
    df = transformer.transform_data(transformer.read_line_data("TF_8", "2026", "03"))

    assert len(df) == 1
    assert float(str(df.iloc[0]["Duracion(min)"]).replace(",", ".")) == 13755.0


def test_no_se_pierde_ni_se_duplica_ninguna_fila(transformer):
    hay_fixture(PAR_IDENTICO[1])
    with PAR_IDENTICO[1].open(encoding="utf-8") as f:
        filas = list(csv.DictReader(f))
    escribir(transformer, filas, mes="04", nombre="abril")

    df = transformer.transform_data(transformer.read_line_data("TF_8", "2026", "04"))
    assert len(df) == len(filas)


# --------------------------------------------------------------------------
# Mes operativo
# --------------------------------------------------------------------------

CASOS = [
    ("2026-03-31 08:00:00", 2026, 3, "2026-03-31"),
    ("2026-03-31 22:00:00", 2026, 3, "2026-03-31"),
    ("2026-04-01 00:00:00", 2026, 3, "2026-03-31"),
    ("2026-04-01 06:00:00", 2026, 3, "2026-03-31"),
    ("2026-04-01 07:59:59", 2026, 3, "2026-03-31"),
    ("2026-04-01 08:00:00", 2026, 4, "2026-04-01"),
    ("2026-04-01 12:00:00", 2026, 4, "2026-04-01"),
]


def test_anio_mes_y_dia_siguen_el_corte_operativo(transformer):
    filas = [segmento(inicio, "2026-04-01 23:00:00") for inicio, _, _, _ in CASOS]
    escribir(transformer, filas, mes="04", nombre="abril")
    df = transformer.transform_data(transformer.read_line_data("TF_8", "2026", "04"))

    for inicio, anio, mes, dia in CASOS:
        fila = df[df["Fecha_Hora_Inicio"] == pd.Timestamp(inicio)].iloc[0]
        assert (int(fila["Año"]), int(fila["Mes"]), str(fila["Dia_Operativo"])) == (anio, mes, dia)


def test_dia_operativo_esta_en_el_orden_de_columnas(transformer):
    escribir(transformer, [segmento("2026-03-10 08:00:00", "2026-03-10 09:00:00")])
    df = transformer.transform_data(transformer.read_line_data("TF_8", "2026", "03"))
    assert list(df.columns)[:3] == ["Año", "Mes", "Dia_Operativo"]


# --------------------------------------------------------------------------
# Hora local de planta
# --------------------------------------------------------------------------

def test_la_ventana_de_desfase_se_corrige_al_entrar(transformer):
    """El reloj del proveedor va +1 h entre el 2o domingo de agosto y el 1o de septiembre.

    Un segmento que el proveedor estampa a las 09:00 es el inicio del turno de
    dia, o sea las 08:00 de planta. Sin corregir, cae en el dia operativo
    anterior y el turno queda mal clasificado.
    """
    escribir(transformer, [segmento("2026-08-20 09:00:00", "2026-08-20 18:00:00")],
             mes="08", nombre="agosto")
    df = transformer.transform_data(transformer.read_line_data("TF_8", "2026", "08"))

    fila = df.iloc[0]
    assert fila["Fecha_Hora_Inicio"] == pd.Timestamp("2026-08-20 08:00:00")
    assert fila["Fecha_Hora_Termino"] == pd.Timestamp("2026-08-20 17:00:00")
    assert str(fila["Dia_Operativo"]) == "2026-08-20"


def test_fuera_de_la_ventana_no_se_toca_nada(transformer):
    """En junio los dos relojes coinciden: corregir romperia el mes."""
    escribir(transformer, [segmento("2026-06-20 08:00:00", "2026-06-20 18:00:00")],
             mes="06", nombre="junio")
    df = transformer.transform_data(transformer.read_line_data("TF_8", "2026", "06"))

    fila = df.iloc[0]
    assert fila["Fecha_Hora_Inicio"] == pd.Timestamp("2026-06-20 08:00:00")
    assert str(fila["Dia_Operativo"]) == "2026-06-20"


def test_el_borde_del_dia_operativo_son_las_ocho_locales(transformer):
    """Dentro de la ventana de desfase, el corte sigue siendo 08:00 de planta."""
    filas = [
        segmento("2026-08-20 08:59:59", "2026-08-20 09:00:00", turn="TN"),  # 07:59:59 local
        segmento("2026-08-20 09:00:00", "2026-08-20 10:00:00"),             # 08:00:00 local
    ]
    escribir(transformer, filas, mes="08", nombre="agosto")
    df = transformer.transform_data(transformer.read_line_data("TF_8", "2026", "08")).sort_values("Fecha_Hora_Inicio")

    assert str(df.iloc[0]["Dia_Operativo"]) == "2026-08-19"
    assert str(df.iloc[1]["Dia_Operativo"]) == "2026-08-20"


# --------------------------------------------------------------------------
# Coherencia entre archivo y columnas
# --------------------------------------------------------------------------

def test_todas_las_filas_de_una_particion_declaran_su_mes(tmp_path, api_falsa):
    """Extractor y transformer deben decidir el mes con la misma regla."""
    from typack_etl.extractor.pipelines import extractor_30m

    fixture = [
        segmento("2026-03-30 08:00:00", "2026-03-30 18:00:00"),
        segmento("2026-03-31 22:00:00", "2026-04-01 06:00:00", turn="TN"),
        segmento("2026-04-01 06:00:00", "2026-04-01 08:00:00", turn="TN"),
        segmento("2026-04-01 08:00:00", "2026-04-01 18:00:00"),
        segmento("2026-04-02 08:00:00", "2026-04-02 18:00:00"),
    ]
    api_falsa(fixture)
    extractor_30m.main([
        "--start-date", "2026-03-25 12:00:00", "--end-date", "2026-04-05 12:00:00",
        "--devid", "1273", "--output", str(tmp_path / "raw"),
        "--logs-dir", str(tmp_path / "logs"), "--audit-dir", str(tmp_path / "audit"),
        "--no-progress",
    ])

    t = DataTransformer(
        base_path=str(tmp_path / "raw"),
        processed_path=str(tmp_path / "processed"),
        consolidated_path=str(tmp_path / "consolidated"),
    )
    marzo = t.transform_data(t.read_line_data("TF_8", "2026", "03"))
    abril = t.transform_data(t.read_line_data("TF_8", "2026", "04"))

    assert set(marzo["Mes"]) == {3}
    assert set(abril["Mes"]) == {4}
    assert len(marzo) + len(abril) == len(fixture)


# --------------------------------------------------------------------------
# Robustez de dtypes
# --------------------------------------------------------------------------

def test_una_columna_enteramente_vacia_no_rompe_las_reglas(transformer):
    """Sin normalizar, pandas la lee como float64 y el accesor .str falla."""
    escribir(transformer, [segmento("2026-03-10 08:00:00", "2026-03-10 09:00:00")])
    df = transformer.transform_data(transformer.read_line_data("TF_8", "2026", "03"))
    assert len(df) == 1
    assert df.iloc[0]["Tipo_registro"] == "produccion"


def test_una_detencion_se_clasifica_como_tal(transformer):
    escribir(transformer, [segmento("2026-03-10 08:00:00", "2026-03-10 09:00:00",
                                    code_description="10.- No planificado", code_t="Operación",
                                    code_f="No planificado", production="50")])
    df = transformer.transform_data(transformer.read_line_data("TF_8", "2026", "03"))
    assert df.iloc[0]["Tipo_registro"] == "detencion"
    assert df.iloc[0]["Q_prod_total"] == 0, "una detención no produce"


def test_el_consolidado_no_lleva_registro_dividido(transformer):
    escribir(transformer, [segmento("2026-03-10 08:00:00", "2026-03-10 09:00:00")])
    df = transformer.transform_data(transformer.read_line_data("TF_8", "2026", "03"))
    transformer.save_processed_data(df, "TF_8", "2026", "03")

    salida = transformer.consolidate_processed_data(["2026"])
    consolidado = pd.read_csv(salida, sep=";", encoding="utf-8-sig")
    assert "Registro_Dividido" not in consolidado.columns
    assert "Dia_Operativo" in consolidado.columns
