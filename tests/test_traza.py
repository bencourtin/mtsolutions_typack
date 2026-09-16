"""Columnas de trazabilidad en la propia fila de datos."""

from datetime import datetime
from pathlib import Path

import pytest

from conftest import segmento
from typack_etl.audit import cdc, traza
from typack_etl.audit.keys import record_key
from typack_etl.audit.ledger import LEDGER_FILENAME, read_ledger
from typack_etl.extractor.functions.io import COLUMNAS_TRAZA, read_csv_rows
from typack_etl.extractor.pipelines import extractor_30m


def dt(texto):
    return datetime.strptime(texto, "%Y-%m-%d %H:%M:%S")


VENTANA = (dt("2026-03-01 00:00:00"), dt("2026-03-31 23:59:59"))


def contexto(run_id="run-2", cuando="2026-03-25 07:00:00", anterior="run-1"):
    return cdc.Contexto(run_id=run_id, devid=1273, linea="TF_8",
                        detectado_en=dt(cuando), run_id_anterior=anterior)


def anotar(nuevas, previas, ctx=None, cambios=None):
    ctx = ctx or contexto()
    if cambios is None:
        cambios = cdc.comparar(previas, nuevas, ctx, *VENTANA).cambios
    return traza.anotar(nuevas, previas, cambios, ctx)


# --------------------------------------------------------------------------
# No mutar lo que recibe
# --------------------------------------------------------------------------

def test_no_ensucia_la_respuesta_de_la_api():
    """Esos diccionarios ya se guardaron crudos en el landing."""
    original = segmento("2026-03-10 08:00:00", "2026-03-10 09:00:00")
    copia = dict(original)
    anotar([original], [])
    assert original == copia


# --------------------------------------------------------------------------
# Alta
# --------------------------------------------------------------------------

def test_un_segmento_nuevo_nace_sin_reasignar():
    fila = anotar([segmento("2026-03-10 08:00:00", "2026-03-10 09:00:00")], [])[0]

    assert fila["reasignada"] == 0
    assert fila["fecha_reasignacion"] == ""
    assert fila["run_id_alta"] == fila["run_id_version"] == "run-2"
    assert fila["record_key"] == record_key(1273, fila)


def test_la_clave_permite_unir_con_el_libro():
    base = segmento("2026-03-10 08:00:00", "2026-03-10 09:00:00", code_description="No asignado")
    nueva = dict(base, code_description="05.- Setup", code_t="Setup")
    ctx = contexto()
    cambios = cdc.comparar([base], [nueva], ctx, *VENTANA).cambios

    fila = traza.anotar([nueva], [base], cambios, ctx)[0]
    assert {c.record_key for c in cambios} == {fila["record_key"]}


# --------------------------------------------------------------------------
# Reasignación
# --------------------------------------------------------------------------

def test_una_reasignacion_marca_la_fila_y_la_fecha():
    base = segmento("2026-03-10 08:00:00", "2026-03-10 09:00:00", code_description="No asignado")
    base.update({"run_id_alta": "run-1", "run_id_version": "run-1",
                 "reasignada": "0", "fecha_reasignacion": ""})
    nueva = segmento("2026-03-10 08:00:00", "2026-03-10 09:00:00",
                     code_description="05.- Setup", code_t="Setup")

    fila = anotar([nueva], [base])[0]
    assert fila["reasignada"] == 1
    assert fila["fecha_reasignacion"] == "2026-03-25"
    assert fila["run_id_alta"] == "run-1", "el alta no se pisa"
    assert fila["run_id_version"] == "run-2", "la versión apunta a la corrida que la cambió"


def test_la_marca_de_reasignacion_no_se_pierde_despues():
    """Una vez reasignada, la fila queda marcada aunque después no cambie."""
    previa = segmento("2026-03-10 08:00:00", "2026-03-10 09:00:00", code_description="05.- Setup")
    previa.update({"run_id_alta": "run-1", "run_id_version": "run-2",
                   "reasignada": "1", "fecha_reasignacion": "2026-03-25"})
    igual = segmento("2026-03-10 08:00:00", "2026-03-10 09:00:00", code_description="05.- Setup")

    fila = anotar([igual], [previa], ctx=contexto(run_id="run-3", cuando="2026-03-30 07:00:00"))[0]
    assert fila["reasignada"] == 1
    assert fila["fecha_reasignacion"] == "2026-03-25", "conserva la fecha original"
    assert fila["run_id_version"] == "run-2", "sin cambios, la versión no avanza"


def test_una_segunda_reasignacion_actualiza_la_fecha():
    previa = segmento("2026-03-10 08:00:00", "2026-03-10 09:00:00", code_description="05.- Setup")
    previa.update({"run_id_alta": "run-1", "reasignada": "1", "fecha_reasignacion": "2026-03-20"})
    nueva = segmento("2026-03-10 08:00:00", "2026-03-10 09:00:00",
                     code_description="07.- Matricería", code_t="Matricería")

    fila = anotar([nueva], [previa], ctx=contexto(run_id="run-3", cuando="2026-03-28 07:00:00"))[0]
    assert fila["fecha_reasignacion"] == "2026-03-28"


def test_editar_un_comentario_no_cuenta_como_reasignacion():
    previa = segmento("2026-03-10 08:00:00", "2026-03-10 09:00:00", code_description="05.- Setup")
    previa.update({"run_id_alta": "run-1", "run_id_version": "run-1", "reasignada": "0"})
    nueva = segmento("2026-03-10 08:00:00", "2026-03-10 09:00:00",
                     code_description="05.- Setup", comments="ajuste de molde")

    fila = anotar([nueva], [previa])[0]
    assert fila["reasignada"] == 0
    assert fila["fecha_reasignacion"] == ""
    assert fila["run_id_version"] == "run-2", "cambió, aunque no sea reasignación"


# --------------------------------------------------------------------------
# Filas anteriores a la traza
# --------------------------------------------------------------------------

def test_a_lo_viejo_se_le_pone_la_clave_pero_no_se_le_inventa_el_alta():
    vieja = segmento("2026-02-01 08:00:00", "2026-02-01 09:00:00")
    fila = traza.rellenar_claves([vieja], 1273)[0]

    assert fila["record_key"] == record_key(1273, vieja)
    assert "run_id_alta" not in fila, "no se puede saber cuándo apareció"


def test_una_fila_sin_traza_previa_no_pierde_su_alta_real():
    sin_traza = segmento("2026-03-10 08:00:00", "2026-03-10 09:00:00")
    igual = dict(sin_traza)
    fila = anotar([igual], [sin_traza])[0]
    assert fila["run_id_alta"] == "run-2", "es lo mejor que se puede saber"


# --------------------------------------------------------------------------
# En el pipeline
# --------------------------------------------------------------------------

@pytest.fixture
def rutas(tmp_path):
    return {k: tmp_path / k for k in ("raw", "logs", "audit", "snapshots")}


FIXTURE = [segmento("2026-03-30 08:00:00", "2026-03-30 18:00:00", code_description="No asignado"),
           segmento("2026-03-30 18:00:00", "2026-03-30 22:00:00")]


def correr(rutas, api_falsa, respuesta):
    api_falsa(respuesta)
    return extractor_30m.main([
        "--start-date", "2026-03-25 12:00:00", "--end-date", "2026-04-05 12:00:00",
        "--devid", "1273", "--output", str(rutas["raw"]), "--logs-dir", str(rutas["logs"]),
        "--audit-dir", str(rutas["audit"]), "--snapshots-dir", str(rutas["snapshots"]),
        "--no-progress",
    ])


def particion(rutas):
    return rutas["raw"] / "2026" / "03" / "produccion_2026_marzo_TF_8.csv"


def test_el_csv_de_datos_lleva_las_columnas_de_traza(rutas, api_falsa):
    correr(rutas, api_falsa, FIXTURE)
    filas = read_csv_rows(particion(rutas))

    for columna in COLUMNAS_TRAZA:
        assert columna in filas[0], f"falta {columna}"
    assert all(f["reasignada"] == "0" for f in filas)
    assert all(f["fecha_reasignacion"] == "" for f in filas)
    assert len({f["run_id_alta"] for f in filas}) == 1


def test_reextraer_lo_identico_deja_el_archivo_igual(rutas, api_falsa):
    """La traza no puede romper la estabilidad byte a byte."""
    correr(rutas, api_falsa, FIXTURE)
    antes = particion(rutas).read_bytes()
    correr(rutas, api_falsa, FIXTURE)
    assert particion(rutas).read_bytes() == antes


def test_una_reasignacion_queda_en_la_fila_y_en_el_libro(rutas, api_falsa, monkeypatch):
    monkeypatch.setattr(extractor_30m, "now_planta", lambda: dt("2026-04-06 07:00:00"))
    correr(rutas, api_falsa, FIXTURE)

    reasignado = [dict(FIXTURE[0], code_description="07.- Matricería", code_t="Matricería")] + FIXTURE[1:]
    correr(rutas, api_falsa, reasignado)

    filas = {f["start"]: f for f in read_csv_rows(particion(rutas))}
    tocada = filas["2026-03-30 08:00:00"]
    intacta = filas["2026-03-30 18:00:00"]

    assert tocada["reasignada"] == "1"
    assert tocada["fecha_reasignacion"] == "2026-04-06"
    assert intacta["reasignada"] == "0"
    assert intacta["fecha_reasignacion"] == ""

    # y desde la fila se llega al detalle en el libro
    libro = read_ledger(rutas["audit"] / LEDGER_FILENAME)
    del_registro = [r for r in libro if r["record_key"] == tocada["record_key"]]
    assert del_registro, "la record_key de la fila debe estar en el libro"
    assert {r["campo"] for r in del_registro} >= {"code_description", "code_t"}


def test_el_retraso_de_asignacion_es_calculable(rutas, api_falsa, monkeypatch):
    """Es la medida que se busca: cuánto tardaron en asignar el código."""
    monkeypatch.setattr(extractor_30m, "now_planta", lambda: dt("2026-04-06 07:00:00"))
    correr(rutas, api_falsa, FIXTURE)
    reasignado = [dict(FIXTURE[0], code_description="07.- Matricería")] + FIXTURE[1:]
    correr(rutas, api_falsa, reasignado)

    fila = next(f for f in read_csv_rows(particion(rutas)) if f["reasignada"] == "1")
    evento = dt(fila["start"]).date()
    deteccion = datetime.strptime(fila["fecha_reasignacion"], "%Y-%m-%d").date()
    assert (deteccion - evento).days == 7


def test_la_version_apunta_al_snapshot_que_la_contiene(rutas, api_falsa):
    """run_id_version es el puente al landing inmutable."""
    from typack_etl.extractor.functions.landing import buscar_en_manifest, leer_snapshot

    correr(rutas, api_falsa, FIXTURE)
    reasignado = [dict(FIXTURE[0], code_description="07.- Matricería")] + FIXTURE[1:]
    correr(rutas, api_falsa, reasignado)

    fila = next(f for f in read_csv_rows(particion(rutas)) if f["reasignada"] == "1")
    entrada = buscar_en_manifest(rutas["snapshots"], run_id=fila["run_id_version"])
    assert entrada, "el run_id_version debe existir en el manifiesto"

    _, registros = leer_snapshot(entrada[0]["snapshot"])
    crudo = next(r for r in registros if r["start"] == fila["start"])
    assert crudo["code_description"] == "07.- Matricería"
