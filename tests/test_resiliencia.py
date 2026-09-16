"""Reintentos, renovación de token, estado de línea y rotación de bitácoras."""

from datetime import datetime, timedelta
from pathlib import Path

import pytest
import requests

from conftest import segmento
from typack_etl.extractor.functions.io import rotar_logs, run_stamp, write_plan_log, write_results_log
from typack_etl.extractor.pipelines import extractor_30m
from typack_etl.extractor.pipelines.extractor_30m import (
    LIMITES_SOSPECHOSOS,
    Sesion,
    _estado_linea,
    _pct_cobertura,
)
from typack_etl.extractor.services.reports import STATUS_REINTENTABLES, TokenExpirado


# --------------------------------------------------------------------------
# Política de reintentos HTTP
# --------------------------------------------------------------------------

def test_el_rate_limit_se_reintenta():
    """429 no estaba en la lista y el rate limit se perdía sin reintentar."""
    assert 429 in STATUS_REINTENTABLES
    for codigo in (500, 502, 503, 504):
        assert codigo in STATUS_REINTENTABLES


def test_la_sesion_respeta_retry_after():
    from typack_etl.extractor.services.reports import _create_session
    adapter = _create_session().get_adapter("https://x")
    assert adapter.max_retries.respect_retry_after_header is True


# --------------------------------------------------------------------------
# Sesión: renovación de token y reintentos por línea
# --------------------------------------------------------------------------

@pytest.fixture
def sesion(monkeypatch):
    """Sesión con tokens numerados y sin esperas reales."""
    emitidos = []

    def emitir():
        emitidos.append(f"token-{len(emitidos) + 1}")
        return emitidos[-1]

    monkeypatch.setattr(extractor_30m, "get_token", emitir)
    monkeypatch.setattr(extractor_30m.time, "sleep", lambda s: None)
    s = Sesion(intentos=3, espera=0)
    s.emitidos = emitidos
    return s


def test_el_token_se_pide_una_sola_vez(sesion, monkeypatch):
    monkeypatch.setattr(extractor_30m, "get_sabana_report", lambda t, s, e, d=None: [])
    for _ in range(5):
        sesion.sabana("s", "e", 1273)
    assert len(sesion.emitidos) == 1
    assert sesion.renovaciones == 0


def test_un_token_expirado_se_renueva_y_la_linea_no_se_pierde(sesion, monkeypatch):
    """Un lote de 11 líneas puede cruzar la expiración a mitad de camino."""
    llamadas = []

    def api(token, start, end, devid=None):
        llamadas.append(token)
        if len(llamadas) == 1:
            raise TokenExpirado("HTTP 401")
        return [segmento("2026-03-01 08:00:00", "2026-03-01 09:00:00")]

    monkeypatch.setattr(extractor_30m, "get_sabana_report", api)
    filas = sesion.sabana("s", "e", 1273)

    assert len(filas) == 1
    assert sesion.renovaciones == 1
    assert llamadas[0] != llamadas[1], "debe reintentar con un token distinto"


def test_un_fallo_de_red_transitorio_se_reintenta(sesion, monkeypatch):
    intentos = []

    def api(token, start, end, devid=None):
        intentos.append(1)
        if len(intentos) < 3:
            raise requests.ConnectionError("la red se cayó")
        return []

    monkeypatch.setattr(extractor_30m, "get_sabana_report", api)
    assert sesion.sabana("s", "e", 1273) == []
    assert len(intentos) == 3
    assert sesion.reintentos == 2


def test_si_la_red_no_vuelve_el_error_se_propaga(sesion, monkeypatch):
    def api(token, start, end, devid=None):
        raise requests.ConnectionError("la red no vuelve")

    monkeypatch.setattr(extractor_30m, "get_sabana_report", api)
    with pytest.raises(requests.ConnectionError):
        sesion.sabana("s", "e", 1273)


def test_un_error_que_no_es_de_red_no_se_reintenta(sesion, monkeypatch):
    intentos = []

    def api(token, start, end, devid=None):
        intentos.append(1)
        raise ValueError("respuesta ilegible")

    monkeypatch.setattr(extractor_30m, "get_sabana_report", api)
    with pytest.raises(ValueError):
        sesion.sabana("s", "e", 1273)
    assert len(intentos) == 1, "reintentar no arregla una respuesta corrupta"


# --------------------------------------------------------------------------
# Estado de línea
# --------------------------------------------------------------------------

def test_una_linea_detenida_no_es_un_fallo():
    """`rows > 0` marcaba como error a una línea legítimamente parada."""
    assert _estado_linea([], [], {}) == "SIN_DATOS"


def test_una_respuesta_en_un_limite_redondo_es_sospechosa():
    filas = [segmento("2026-03-01 08:00:00", "2026-03-01 09:00:00")] * LIMITES_SOSPECHOSOS[0]
    assert _estado_linea(filas, [], {}) == "TRUNCADO_SOSPECHOSO"


def test_una_caida_de_cobertura_degrada_el_estado():
    filas = [segmento("2026-03-01 08:00:00", "2026-03-01 09:00:00")]
    caidas = [{"linea": "TF_8", "fecha": "2026-03-01", "delta": -180.0}]
    assert _estado_linea(filas, caidas, {"2026-03-01": 1260.0}) == "COBERTURA_BAJA"


def test_una_linea_normal_esta_ok():
    filas = [segmento("2026-03-01 08:00:00", "2026-03-01 09:00:00")]
    assert _estado_linea(filas, [], {"2026-03-01": 1440.0}) == "OK"


@pytest.mark.parametrize("cobertura,esperado", [
    ({}, 0.0),
    ({"2026-03-01": 1440.0}, 100.0),
    ({"2026-03-01": 720.0}, 50.0),
    ({"2026-03-01": 1440.0, "2026-03-02": 720.0}, 75.0),
])
def test_pct_cobertura(cobertura, esperado):
    assert _pct_cobertura(cobertura) == esperado


# --------------------------------------------------------------------------
# Rotación de bitácoras
# --------------------------------------------------------------------------

def test_borra_las_bitacoras_viejas_y_conserva_las_recientes(tmp_path):
    from typack_etl.extractor.utils.date_utils import now_planta
    hoy = now_planta()
    for dias in (200, 120, 91, 89, 10, 0):
        stamp = (hoy - timedelta(days=dias)).strftime("%Y%m%d_%H%M%S")
        write_plan_log([], tmp_path, stamp)
        write_results_log([], tmp_path, stamp)

    borrados = rotar_logs(tmp_path, 90)

    assert borrados == 6, "tres corridas viejas, dos archivos cada una"
    assert len(list(tmp_path.glob("plan_*.csv"))) == 3
    assert len(list(tmp_path.glob("results_*.csv"))) == 3


def test_no_toca_el_libro_de_auditoria(tmp_path):
    """El libro es justamente el histórico que hay que conservar."""
    libro = tmp_path / "detenciones_audit.csv"
    libro.write_text("record_key;tipo_cambio\n", encoding="utf-8-sig")
    rotar_logs(tmp_path, 1)
    assert libro.exists()


def test_no_entra_en_subcarpetas(tmp_path):
    """Las del extractor mensual viven en <logs>/<año>/<mes> y no se tocan."""
    sub = tmp_path / "2025" / "01"
    write_plan_log([], sub, "20250101_000000")
    assert rotar_logs(tmp_path, 90) == 0
    assert (sub / "plan_20250101_000000.csv").exists()


def test_un_nombre_inesperado_no_se_borra(tmp_path):
    raro = tmp_path / "results_respaldo_manual.csv"
    raro.write_text("x", encoding="utf-8")
    rotar_logs(tmp_path, 1)
    assert raro.exists()


def test_retencion_cero_desactiva_la_rotacion(tmp_path):
    write_plan_log([], tmp_path, "20200101_000000")
    assert rotar_logs(tmp_path, 0) == 0
    assert len(list(tmp_path.glob("plan_*.csv"))) == 1


def test_un_directorio_inexistente_no_rompe(tmp_path):
    assert rotar_logs(tmp_path / "no_existe", 90) == 0
