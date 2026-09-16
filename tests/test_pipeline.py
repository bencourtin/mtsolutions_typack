"""El extractor de punta a punta, con la API simulada."""

import collections
from datetime import datetime
from pathlib import Path

import pytest

from conftest import leer_csv, segmento
from typack_etl.audit import cdc
from typack_etl.audit.ledger import LEDGER_FILENAME, leer_ultimas_corridas, read_ledger
from typack_etl.extractor.functions.io import read_csv_rows
from typack_etl.extractor.pipelines import extractor_30m, extractor_mensual


def dt(texto):
    return datetime.strptime(texto, "%Y-%m-%d %H:%M:%S")


@pytest.fixture
def rutas(tmp_path):
    return {
        "raw": tmp_path / "raw",
        "logs": tmp_path / "logs",
        "audit": tmp_path / "audit",
    }


def correr(rutas, extra=()):
    return extractor_30m.main([
        "--start-date", "2026-03-25 12:00:00",
        "--end-date", "2026-04-05 12:00:00",
        "--devid", "1273",
        "--output", str(rutas["raw"]),
        "--logs-dir", str(rutas["logs"]),
        "--audit-dir", str(rutas["audit"]),
        "--no-progress",
        *extra,
    ])


def particion(rutas, mes, nombre):
    return rutas["raw"] / "2026" / mes / f"produccion_2026_{nombre}_TF_8.csv"


# --------------------------------------------------------------------------
# Particionado por mes operativo
# --------------------------------------------------------------------------

CRUCE_DE_MES = [
    segmento("2026-03-31 08:00:00", "2026-03-31 18:00:00"),              # marzo
    segmento("2026-03-31 22:00:00", "2026-04-01 06:00:00", turn="TN"),   # marzo
    segmento("2026-04-01 06:00:00", "2026-04-01 08:00:00", turn="TN"),   # marzo
    segmento("2026-04-01 08:00:00", "2026-04-01 18:00:00"),              # abril
    segmento("2026-04-01 09:00:00", "2026-04-01 10:00:00"),              # abril
]


def test_los_registros_caen_en_el_archivo_del_mes_operativo(rutas, api_falsa):
    api_falsa(CRUCE_DE_MES)
    assert correr(rutas) == 0

    marzo = {r["start"] for r in read_csv_rows(particion(rutas, "03", "marzo"))}
    abril = {r["start"] for r in read_csv_rows(particion(rutas, "04", "abril"))}

    assert "2026-03-31 22:00:00" in marzo, "el turno de noche del 31 es de marzo"
    assert "2026-04-01 06:00:00" in marzo, "la madrugada del 1 todavía es marzo"
    assert "2026-04-01 08:00:00" in abril, "el corte abre abril"
    assert len(marzo) == 3 and len(abril) == 2
    assert len(marzo) + len(abril) == len(CRUCE_DE_MES), "no se pierde ningún registro"


def test_el_nombre_del_archivo_usa_la_linea_y_no_el_devid(rutas, api_falsa):
    """El transformer busca por nombre de línea; con el devid no lo levantaría."""
    api_falsa(CRUCE_DE_MES)
    correr(rutas)
    assert particion(rutas, "03", "marzo").exists()


# --------------------------------------------------------------------------
# Aislamiento de fallos
# --------------------------------------------------------------------------

def test_una_linea_que_falla_no_toca_sus_archivos(rutas, api_falsa):
    api_falsa(CRUCE_DE_MES)
    correr(rutas)
    antes = particion(rutas, "03", "marzo").read_bytes()

    def revienta(token, start, end, devid=None):
        raise RuntimeError("500 desde la API")

    api_falsa(revienta)
    assert correr(rutas) == 1, "si fallan todas las líneas, la corrida debe reportar fallo"
    assert particion(rutas, "03", "marzo").read_bytes() == antes


def test_si_solo_algunas_lineas_fallan_la_corrida_sigue(rutas, api_falsa):
    """Una línea caída no puede dejar sin actualizar a las otras diez."""
    def intermitente(token, start, end, devid=None):
        if str(devid) == "1273":
            raise RuntimeError("500 desde la API")
        return CRUCE_DE_MES

    api_falsa(intermitente)
    codigo = extractor_30m.main([
        "--start-date", "2026-03-25 12:00:00", "--end-date", "2026-04-05 12:00:00",
        "--output", str(rutas["raw"]), "--logs-dir", str(rutas["logs"]),
        "--audit-dir", str(rutas["audit"]), "--no-progress",
    ])
    assert codigo == 0

    filas = leer_csv(sorted(rutas["logs"].glob("results_*.csv"))[-1])
    estados = collections.Counter(r["estado"] for r in filas)
    assert estados["ERROR"] == 1
    assert estados["OK"] == len(filas) - 1


def test_el_fallo_queda_en_la_bitacora(rutas, api_falsa):
    def revienta(token, start, end, devid=None):
        raise RuntimeError("500 desde la API")

    api_falsa(revienta)
    correr(rutas)

    resultados = sorted(rutas["logs"].glob("results_*.csv"))[-1]
    fila = leer_csv(resultados)[0]
    assert fila["success"] == "0"
    assert "500 desde la API" in fila["error"]


def test_el_plan_se_escribe_antes_de_consultar(rutas, api_falsa):
    def revienta(token, start, end, devid=None):
        raise RuntimeError("caída total")

    api_falsa(revienta)
    correr(rutas)
    assert sorted(rutas["logs"].glob("plan_*.csv")), "el plan debe existir aunque todo falle"


# --------------------------------------------------------------------------
# Auditoría integrada
# --------------------------------------------------------------------------

def test_la_primera_corrida_no_genera_cambios(rutas, api_falsa):
    api_falsa(CRUCE_DE_MES)
    correr(rutas)
    assert read_ledger(rutas["audit"] / LEDGER_FILENAME) == [], "todo es alta"


def test_reejecutar_lo_identico_no_genera_cambios(rutas, api_falsa):
    api_falsa(CRUCE_DE_MES)
    correr(rutas)
    correr(rutas)
    correr(rutas)
    assert read_ledger(rutas["audit"] / LEDGER_FILENAME) == []


def test_una_reasignacion_queda_registrada(rutas, api_falsa, monkeypatch):
    monkeypatch.setattr(extractor_30m, "now_planta", lambda: dt("2026-04-07 07:00:00"))
    api_falsa(CRUCE_DE_MES)
    correr(rutas)

    reasignado = [dict(CRUCE_DE_MES[0],
                       code_description="07.- Matricería no planificada",
                       code_t="Matricería", user="J. SOTO")] + CRUCE_DE_MES[1:]
    api_falsa(reasignado)
    correr(rutas)

    libro = read_ledger(rutas["audit"] / LEDGER_FILENAME)
    fila = next(r for r in libro if r["campo"] == "code_description")
    assert fila["tipo_cambio"] == cdc.REASIGNACION
    assert fila["valor_anterior"] == ""
    assert "Matricería" in fila["valor_nuevo"]
    assert fila["usuario_nuevo"] == "J. SOTO"
    assert fila["dias_desde_evento"] == "7"
    assert fila["run_id_anterior"] and fila["run_id_anterior"] != fila["run_id_actual"]


def test_el_archivo_de_datos_refleja_el_codigo_nuevo(rutas, api_falsa):
    api_falsa(CRUCE_DE_MES)
    correr(rutas)
    reasignado = [dict(CRUCE_DE_MES[0], code_description="07.- Matricería")] + CRUCE_DE_MES[1:]
    api_falsa(reasignado)
    correr(rutas)

    filas = {r["start"]: r for r in read_csv_rows(particion(rutas, "03", "marzo"))}
    assert filas["2026-03-31 08:00:00"]["code_description"] == "07.- Matricería"


def test_registra_la_ultima_corrida_por_linea(rutas, api_falsa):
    api_falsa(CRUCE_DE_MES)
    correr(rutas)
    assert "TF_8" in leer_ultimas_corridas(rutas["audit"])


def test_no_audit_corre_sin_escribir_libro(rutas, api_falsa):
    api_falsa(CRUCE_DE_MES)
    assert correr(rutas, ["--no-audit"]) == 0
    assert not (rutas["audit"] / LEDGER_FILENAME).exists()


def test_la_bitacora_resume_los_cambios_por_particion(rutas, api_falsa):
    api_falsa(CRUCE_DE_MES)
    correr(rutas)
    resultados = sorted(rutas["logs"].glob("results_*.csv"))[-1]
    detalle = leer_csv(resultados)[0]["detalle"]
    assert "2026-03" in detalle and "2026-04" in detalle
    assert "cambios:" in detalle


# --------------------------------------------------------------------------
# Extractor mensual: recargas históricas
# --------------------------------------------------------------------------

def test_una_recarga_parcial_no_borra_el_resto_del_mes(rutas, api_falsa, tmp_path):
    destino = tmp_path / "produccion_2026_mayo_TF_9.csv"
    from typack_etl.extractor.functions.io import write_csv
    write_csv([segmento(f"2026-05-{d:02d} 10:00:00", f"2026-05-{d:02d} 11:00:00", production="10")
               for d in range(1, 32)], destino)

    # La ventana en planta va del 1-may 08:00 al 16-may 08:00, así que la API
    # debe reportar los días 1 a 15: lo que no reporte dentro de la ventana se
    # borra, que es justamente el comportamiento deseado.
    api_falsa([segmento(f"2026-05-{d:02d} 10:00:00", f"2026-05-{d:02d} 11:00:00", production="999")
               for d in range(1, 16)])
    extractor_mensual.main([
        "--start-date", "2026-05-01 12:00:00",
        "--end-date", "2026-05-16 12:00:00",
        "--devid", "1274",
        "--output", str(destino),
        "--logs-dir", str(rutas["logs"]),
    ])

    filas = {r["start"]: r for r in read_csv_rows(destino)}
    assert len(filas) == 31, "el resto del mes debe conservarse"
    assert filas["2026-05-10 10:00:00"]["production"] == "999", "el tramo recargado se actualiza"
    assert filas["2026-05-20 10:00:00"]["production"] == "10", "lo de afuera queda intacto"


def test_lo_que_la_api_no_reporta_dentro_del_rango_se_borra(rutas, api_falsa, tmp_path):
    """Contracara del test anterior: dentro de la ventana manda la API."""
    destino = tmp_path / "produccion_2026_mayo_TF_9.csv"
    from typack_etl.extractor.functions.io import write_csv
    write_csv([segmento(f"2026-05-{d:02d} 10:00:00", f"2026-05-{d:02d} 11:00:00")
               for d in range(1, 32)], destino)

    api_falsa([segmento(f"2026-05-{d:02d} 10:00:00", f"2026-05-{d:02d} 11:00:00")
               for d in range(1, 16) if d != 7])
    extractor_mensual.main([
        "--start-date", "2026-05-01 12:00:00",
        "--end-date", "2026-05-16 12:00:00",
        "--devid", "1274",
        "--output", str(destino),
        "--logs-dir", str(rutas["logs"]),
    ])

    filas = {r["start"] for r in read_csv_rows(destino)}
    assert "2026-05-07 10:00:00" not in filas
    assert len(filas) == 30
