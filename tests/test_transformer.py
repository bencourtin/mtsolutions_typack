"""Reglas de negocio: hora local, mes operativo y fragmentación por turno."""

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
# Fragmentación por turno
# --------------------------------------------------------------------------
#
# Cada fila se parte en un tramo por cada turno que su intervalo cruza (ver
# `Horarios.tramos`): TD/TT/TN cuando cae dentro de una franja de
# horarios.json, TNL cuando no (fin de semana, el hueco entre turnos, o un
# feriado marcado en Comentarios). El split viejo (un corte fijo a
# medianoche, sin importar el turno) se sacó porque perdía los días del medio
# en intervalos largos -- `test_un_registro_de_varios_dias_conserva_su_duracion`
# es justamente el caso que lo destapó, y sigue valiendo con el split nuevo
# porque ese intervalo cae antes de toda vigencia (no hay horario contra el
# cual fragmentar) y se conserva entero.

def test_ejemplo_base_TD_hueco_TN(transformer):
    """TF5 16:00->23:37 TD: se parte en TD/TNL/TN, sin perder duración ni producción."""
    escribir(transformer, [segmento("2026-09-22 16:00:00", "2026-09-22 23:37:00",
                                    production="300", turn="TD")],
             mes="09", nombre="septiembre")
    df = transformer.transform_data(
        transformer.read_line_data("TF_8", "2026", "09")
    ).sort_values("Fecha_Hora_Inicio").reset_index(drop=True)

    assert len(df) == 3
    assert list(df["Turno"]) == ["TD", "TNL", "TN"]
    assert list(df["Turno_Origen"]) == ["TD", "TD", "TD"]
    assert list(df["Fecha_Inicio_Turno"].astype(str)) == ["2026-09-22"] * 3
    assert [str(t) for t in df["Fecha_Hora_Inicio"]] == [
        "2026-09-22 16:00:00", "2026-09-22 18:00:00", "2026-09-22 23:00:00",
    ]
    assert [str(t) for t in df["Fecha_Hora_Termino"]] == [
        "2026-09-22 18:00:00", "2026-09-22 23:00:00", "2026-09-22 23:37:00",
    ]
    # Duración y producción se reparten sin perder nada por redondeo.
    assert sum(df["Duracion(min)"]) == pytest.approx(7 * 60 + 37)
    assert sum(df["Q_prod_total"]) == pytest.approx(300.0)


def test_fin_de_semana_T0_se_declara_TNL(transformer):
    """Un tramo T0 de fin de semana (sábado a lunes) se declara TNL de punta a punta."""
    escribir(transformer, [segmento("2026-09-19 20:00:00", "2026-09-21 09:00:00",
                                    production="0", turn="T0")],
             mes="09", nombre="septiembre")
    df = transformer.transform_data(
        transformer.read_line_data("TF_8", "2026", "09")
    ).sort_values("Fecha_Hora_Inicio").reset_index(drop=True)

    assert len(df) == 2
    assert list(df["Turno"]) == ["TNL", "TD"]
    assert list(df["Turno_Origen"]) == ["T0", "T0"]
    assert str(df.iloc[0]["Fecha_Hora_Termino"]) == "2026-09-21 08:00:00"
    assert str(df.iloc[1]["Fecha_Hora_Inicio"]) == "2026-09-21 08:00:00"


def test_feriado_en_dia_de_semana_pisa_el_turno_declarado(transformer):
    """Un feriado marcado en Comentarios es TNL aunque el origen ya haya declarado TD."""
    escribir(transformer, [segmento("2026-09-08 10:00:00", "2026-09-08 12:00:00",
                                    turn="TD", production="80", comments="FERIADO")],
             mes="09", nombre="septiembre")
    df = transformer.transform_data(transformer.read_line_data("TF_8", "2026", "09"))

    assert len(df) == 1
    assert df.iloc[0]["Turno"] == "TNL"
    assert df.iloc[0]["Turno_Origen"] == "TD"
    assert df.iloc[0]["Q_prod_total"] == 80


def test_el_metodo_del_split_de_medianoche_ya_no_existe():
    """El split viejo -- un corte fijo a medianoche, sin importar el turno -- no vuelve."""
    assert not hasattr(DataTransformer, "_split_midnight_crossings")
    assert "Registro_Dividido" not in FUENTE.read_text(encoding="utf-8")


def test_un_registro_que_cruza_medianoche_dentro_del_mismo_turno_sigue_siendo_uno(transformer):
    """El cruce de medianoche no fragmenta por sí solo: sólo lo hace un borde de turno real."""
    escribir(transformer, [segmento("2026-03-10 22:00:00", "2026-03-11 04:33:00",
                                    production="4300", duration="393.0", turn="TN")])
    df = transformer.transform_data(transformer.read_line_data("TF_8", "2026", "03"))

    assert len(df) == 1
    assert int(df.iloc[0]["Q_prod_total"]) == 4300
    assert str(df.iloc[0]["Fecha_Hora_Termino"]) == "2026-03-11 04:33:00"
    assert str(df.iloc[0]["Fecha_Inicio_Turno"]) == "2026-03-10"
    assert "Registro_Dividido" not in df.columns


def test_un_registro_de_varios_dias_conserva_su_duracion(transformer):
    """El split de dos tramos descartaba los días del medio.

    Este intervalo cae antes de toda vigencia de horarios.json (la más
    antigua empieza 2026-04-01): no hay horario contra el cual fragmentar, así
    que se conserva entero, con el turno de origen, igual que hoy hace
    `clasificar()` para `Caso.SIN_VIGENCIA`.
    """
    escribir(transformer, [segmento("2026-03-10 21:00:00", "2026-03-20 10:14:58",
                                    duration="13755.0", production="0", turn="TN")])
    df = transformer.transform_data(transformer.read_line_data("TF_8", "2026", "03"))

    assert len(df) == 1
    assert float(str(df.iloc[0]["Duracion(min)"]).replace(",", ".")) == 13755.0
    assert df.iloc[0]["Turno"] == "TN"


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


# --------------------------------------------------------------------------
# Precisión numérica para Excel
# --------------------------------------------------------------------------
#
# `duration` llega del raw con coma decimal ("13755,0"), así que pandas lo lee
# como texto, no como número -- y una división exacta en segundos (1 seg =
# 1/60 min) cae en un binario periódico de 16-20 dígitos
# ("0,016666666666666666"), más de los 15 dígitos significativos que Excel
# puede representar: los trunca o corrompe en silencio al abrir el CSV.

def test_duracion_no_supera_los_15_digitos_significativos_de_excel(transformer):
    """Un tramo de 1 segundo (1/60 min) es el caso que más decimales genera."""
    escribir(transformer, [segmento("2026-09-22 17:59:59", "2026-09-22 18:00:30",
                                    turn="TD", production="0")],
             mes="09", nombre="septiembre")
    df = transformer.transform_data(transformer.read_line_data("TF_8", "2026", "09"))

    assert len(df) == 2  # cruza el borde TD/TNL de las 18:00
    for valor in df["Duracion(min)"]:
        texto = str(valor).replace(",", ".").split(".")
        decimales = texto[1] if len(texto) > 1 else ""
        assert len(decimales) <= 4, f"{valor!r} tiene más de 4 decimales"


def test_duracion_con_coma_decimal_del_raw_se_redondea_sin_perderse(transformer):
    """`duration="13755,0"` (coma, como llega del proveedor) no debe volverse NaN."""
    escribir(transformer, [segmento("2026-03-10 21:00:00", "2026-03-20 10:14:58",
                                    duration="13755,0", production="0", turn="TN")])
    df = transformer.transform_data(transformer.read_line_data("TF_8", "2026", "03"))

    assert len(df) == 1
    assert df.iloc[0]["Duracion(min)"] == pytest.approx(13755.0)


# --------------------------------------------------------------------------
# TN del viernes entrado el sábado (horarios.json no define sábado)
# --------------------------------------------------------------------------

def test_fecha_inicio_turno_de_una_fila_ya_en_sabado_es_el_viernes(transformer):
    """Caso real (2026-07-25): una micro-parada TN a las 01:37 del sábado es la
    cola del TN del viernes, que no tiene horario propio en sábado. Antes se
    declaraba con Fecha_Inicio_Turno = sábado (la fecha de la propia fila);
    debe ser viernes, el día en que ese turno arrancó.
    """
    escribir(transformer, [segmento("2026-07-25 01:37:40", "2026-07-25 01:37:47",
                                    turn="TN", production="0")],
             mes="07", nombre="julio")
    df = transformer.transform_data(transformer.read_line_data("TF_8", "2026", "07"))

    assert len(df) == 1
    assert df.iloc[0]["Turno"] == "TN"
    assert str(df.iloc[0]["Fecha_Inicio_Turno"]) == "2026-07-24"


def test_lunes_de_madrugada_sin_TN_de_domingo_no_se_declara_TN(transformer):
    """Caso real (2026-09-07, lunes): filas TN a las 00:00 y ~07:50-07:59
    quedaban declaradas TN con Fecha_Inicio_Turno=domingo -- un día sin TN
    definido -- porque la franja propia de "TN lunes" (23:00->08:00) se leía
    sin distinguir la noche del lunes (válida) de su propia madrugada (que
    sólo sería válida si domingo tuviera TN, y no lo tiene).
    """
    escribir(transformer, [segmento("2026-09-07 00:00:03", "2026-09-07 00:00:10",
                                    turn="TN", production="0")],
             mes="09", nombre="septiembre")
    df = transformer.transform_data(transformer.read_line_data("TF_8", "2026", "09"))

    assert len(df) == 1
    assert df.iloc[0]["Turno"] == "TNL"
    assert df.iloc[0]["Turno_Origen"] == "TN"
    assert str(df.iloc[0]["Fecha_Inicio_Turno"]) == "2026-09-07"


def test_el_TN_del_viernes_entrado_el_sabado_calcula_bien_el_turno(transformer):
    """2026-06-20 es sábado; el TN del viernes (21:30->08:30) sigue corriendo.

    Antes de `franja_vigente`/`ventana_turno`, una fila cuyo `start` ya caía en
    el sábado quedaba sin `Hora_Termino_Turno`, sin `Periodo_Laboral` y sin
    minutos extra aunque el origen ya trajera `TN`, porque sábado no tiene
    horario propio en `horarios.json`. Ese mismo cómputo ahora decide además
    dónde cortar el tramo: la segunda fila sigue produciendo después de las
    08:30, así que se fragmenta en un tramo TN (hasta las 08:30) y uno TNL (lo
    que sigue) -- lo extra ya no se mide comparando contra la hora de fin,
    es directamente la duración del tramo TNL con producción.
    """
    filas = [
        # Dentro del turno: no corresponde hora extra, no se fragmenta.
        segmento("2026-06-20 02:00:00", "2026-06-20 03:00:00",
                 turn="TN", production="100"),
        # Sigue trabajando después de las 08:30: eso se parte en TN + TNL.
        segmento("2026-06-20 08:00:00", "2026-06-20 09:15:00",
                 turn="TN", production="100"),
    ]
    escribir(transformer, filas, mes="06", nombre="junio")
    df = transformer.transform_data(
        transformer.read_line_data("TF_8", "2026", "06")
    ).sort_values("Fecha_Hora_Inicio").reset_index(drop=True)

    assert len(df) == 3
    dentro_del_turno, tramo_tn, tramo_tnl = df.iloc[0], df.iloc[1], df.iloc[2]

    assert dentro_del_turno["Turno"] == "TN"
    assert dentro_del_turno["Hora_Termino_Turno"] == "08:30"
    assert dentro_del_turno["Periodo_Laboral"] == "Sí"
    assert float(str(dentro_del_turno["Minutos_Extras"]).replace(",", ".")) == 0.0

    assert tramo_tn["Turno"] == "TN"
    assert str(tramo_tn["Fecha_Hora_Termino"]) == "2026-06-20 08:30:00"
    assert tramo_tn["Hora_Termino_Turno"] == "08:30"
    assert float(str(tramo_tn["Minutos_Extras"]).replace(",", ".")) == 0.0

    assert tramo_tnl["Turno"] == "TNL"
    assert tramo_tnl["Turno_Origen"] == "TN"
    assert tramo_tnl["Periodo_Laboral"] == "No"
    assert float(str(tramo_tnl["Minutos_Extras"]).replace(",", ".")) == pytest.approx(45.0)


def test_el_consolidado_no_lleva_registro_dividido(transformer):
    escribir(transformer, [segmento("2026-03-10 08:00:00", "2026-03-10 09:00:00")])
    df = transformer.transform_data(transformer.read_line_data("TF_8", "2026", "03"))
    transformer.save_processed_data(df, "TF_8", "2026", "03")

    salida = transformer.consolidate_processed_data(["2026"])
    consolidado = pd.read_csv(salida, sep=";", encoding="utf-8-sig")
    assert "Registro_Dividido" not in consolidado.columns
    assert "Dia_Operativo" in consolidado.columns
