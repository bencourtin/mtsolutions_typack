"""Decisiones del ETL diario: cuándo transformar y cómo marcar el resultado."""

import csv
import json
from pathlib import Path

import pytest

from config.settings import ETL_MAX_LINEAS_FALLIDAS
from scripts import run_daily_etl as orq


@pytest.fixture(autouse=True)
def rutas_del_orquestador(monkeypatch, sin_produccion):
    """El orquestador tiene su propia copia de PATHS."""
    monkeypatch.setattr(orq, "PATHS", sin_produccion)
    for clave in ("logs", "consolidated"):
        Path(sin_produccion[clave]).mkdir(parents=True, exist_ok=True)
    return sin_produccion


def escribir_resultado(rutas, filas, stamp="20260827_120000"):
    destino = Path(rutas["logs"]) / f"results_{stamp}.csv"
    columnas = ["line", "devid", "start", "end", "rows",
                "success", "estado", "cobertura_pct", "error", "detalle"]
    with destino.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columnas, delimiter=";")
        w.writeheader()
        for fila in filas:
            w.writerow({c: fila.get(c, "") for c in columnas})
    return destino


def linea(nombre, estado="OK", rows=100, error=""):
    return {"line": nombre, "devid": "1", "rows": rows, "estado": estado,
            "success": "0" if estado == "ERROR" else "1",
            "cobertura_pct": "100.0", "error": error}


# --------------------------------------------------------------------------
# Lectura del resultado
# --------------------------------------------------------------------------

def test_sin_results_no_inventa_un_resumen(rutas_del_orquestador):
    assert orq.leer_ultimo_resultado() is None


def test_resume_la_corrida_desde_el_csv(rutas_del_orquestador):
    escribir_resultado(rutas_del_orquestador, [
        linea("TF_2"), linea("TF_3"), linea("TF_5", estado="ERROR", rows=0, error="500"),
    ])
    r = orq.leer_ultimo_resultado()
    assert (r["total"], r["ok"], r["fallidas"]) == (3, 2, 1)
    assert r["filas"] == 200
    assert r["lineas_fallidas"] == ["TF_5"]


def test_toma_el_results_mas_reciente(rutas_del_orquestador):
    escribir_resultado(rutas_del_orquestador, [linea("TF_2")], stamp="20260827_080000")
    escribir_resultado(rutas_del_orquestador, [linea("TF_2"), linea("TF_3")], stamp="20260827_190000")
    assert orq.leer_ultimo_resultado()["total"] == 2


def test_los_estados_de_aviso_llegan_al_log(rutas_del_orquestador):
    escribir_resultado(rutas_del_orquestador, [
        linea("TF_2"), linea("TF_3", estado="SIN_DATOS", rows=0),
        linea("TF_5", estado="COBERTURA_BAJA"),
    ])
    alertas = " ".join(orq.leer_ultimo_resultado()["alertas"])
    assert "TF_3" in alertas and "SIN_DATOS" in alertas
    assert "TF_5" in alertas and "COBERTURA_BAJA" in alertas


def test_una_linea_sin_datos_no_cuenta_como_fallo(rutas_del_orquestador):
    """Una línea legítimamente detenida reporta cero, y eso es un dato."""
    escribir_resultado(rutas_del_orquestador, [linea("TF_2"), linea("TF_3", estado="SIN_DATOS", rows=0)])
    assert orq.leer_ultimo_resultado()["fallidas"] == 0


# --------------------------------------------------------------------------
# Umbral de líneas fallidas
# --------------------------------------------------------------------------

def test_el_umbral_esta_configurado():
    assert ETL_MAX_LINEAS_FALLIDAS >= 0


def _correr_extractor(monkeypatch, logger, returncode=0):
    class Resultado:
        def __init__(self):
            self.returncode = returncode
            self.stdout = ""
            self.stderr = ""

    monkeypatch.setattr(orq.subprocess, "run", lambda *a, **kw: Resultado())
    return orq.run_extractor(logger)


def test_con_pocas_fallas_se_transforma(monkeypatch, rutas_del_orquestador, caplog):
    escribir_resultado(rutas_del_orquestador,
                       [linea(f"TF_{i}") for i in range(10)] + [linea("TF_9", estado="ERROR", rows=0)])
    import logging
    ok, filas, error = _correr_extractor(monkeypatch, logging.getLogger("t"))
    assert ok is True and error == ""
    assert filas == 1000


def test_con_demasiadas_fallas_no_se_transforma(monkeypatch, rutas_del_orquestador):
    fallidas = ETL_MAX_LINEAS_FALLIDAS + 1
    filas = [linea(f"TF_{i}") for i in range(3)]
    filas += [linea(f"EXT_{i}", estado="ERROR", rows=0, error="timeout") for i in range(fallidas)]
    escribir_resultado(rutas_del_orquestador, filas)

    import logging
    ok, _, error = _correr_extractor(monkeypatch, logging.getLogger("t"))
    assert ok is False
    assert "No se transforma" in error


def test_si_el_extractor_no_deja_bitacora_se_considera_fallo(monkeypatch, rutas_del_orquestador):
    import logging
    ok, _, error = _correr_extractor(monkeypatch, logging.getLogger("t"))
    assert ok is False
    assert "results_*.csv" in error


# --------------------------------------------------------------------------
# Marca del consolidado
# --------------------------------------------------------------------------

def test_marca_el_consolidado_con_el_estado(rutas_del_orquestador):
    orq.marcar_consolidado("PARCIAL", {"lineas_fallidas": ["TF_5"], "consolidado_actualizado": True})
    destino = Path(rutas_del_orquestador["consolidated"]) / "mt_typ_consolidated.estado.json"

    datos = json.loads(destino.read_text(encoding="utf-8"))
    assert datos["estado"] == "PARCIAL"
    assert datos["lineas_fallidas"] == ["TF_5"]


def test_la_marca_no_tumba_el_etl_si_no_puede_escribir(monkeypatch, rutas_del_orquestador):
    monkeypatch.setattr(orq.Path, "mkdir", lambda *a, **kw: (_ for _ in ()).throw(OSError("sin permiso")))
    orq.marcar_consolidado("OK", {})  # no debe levantar


# --------------------------------------------------------------------------
# Publicación de la auditoría
# --------------------------------------------------------------------------

class TrackerFalso:
    """Evita que el tracker real escriba en el directorio de logs de producción."""

    registros = []

    def record_execution(self, **kw):
        TrackerFalso.registros.append(kw)


def correr_etl(monkeypatch, trans_success=True):
    """Ejecuta el ETL diario con extractor y transformer simulados.

    Devuelve `(llamadas_a_publicar_auditoria, codigo_de_salida)`.
    """
    import logging

    llamadas = []
    TrackerFalso.registros = []
    monkeypatch.setattr(orq, "run_extractor", lambda logger: (True, 100, ""))
    monkeypatch.setattr(
        orq, "run_transformer",
        lambda logger: (trans_success, 100 if trans_success else 0,
                        "" if trans_success else "falló la transformación"),
    )
    monkeypatch.setattr(orq, "send_error_alert", lambda **kw: True)
    monkeypatch.setattr(orq, "StatsTracker", TrackerFalso)
    monkeypatch.setattr(orq, "setup_logging", lambda: logging.getLogger("t"))
    monkeypatch.setattr(orq, "publicar_auditoria",
                        lambda logger: llamadas.append(logger) or {"filas_reasignaciones": 3})

    codigo = orq.run_daily_etl()
    return llamadas, codigo


def test_se_publica_la_auditoria_tras_consolidar(monkeypatch, rutas_del_orquestador):
    llamadas, codigo = correr_etl(monkeypatch, trans_success=True)
    assert len(llamadas) == 1
    assert codigo == 0


def test_sin_consolidado_no_se_publica_la_auditoria(monkeypatch, rutas_del_orquestador):
    """Reasignaciones frescas junto a un consolidado viejo mienten en el BI."""
    llamadas, _ = correr_etl(monkeypatch, trans_success=False)
    assert llamadas == []


def test_el_sidecar_informa_si_se_publico(monkeypatch, rutas_del_orquestador):
    correr_etl(monkeypatch, trans_success=True)
    destino = Path(rutas_del_orquestador["consolidated"]) / "mt_typ_consolidated.estado.json"

    datos = json.loads(destino.read_text(encoding="utf-8"))
    assert datos["auditoria_publicada"] is True
    assert datos["reasignaciones_publicadas"] == 3


def test_un_fallo_al_publicar_no_tumba_el_etl(monkeypatch, rutas_del_orquestador, caplog):
    """El consolidado ya está en su lugar: la auditoría es complementaria."""
    import logging

    def explota(*a, **kw):
        raise OSError("la unidad de red no responde")

    monkeypatch.setattr(orq, "publicar", explota)
    with caplog.at_level(logging.WARNING):
        assert orq.publicar_auditoria(logging.getLogger("t")) == {}
    assert "No se pudo publicar la auditoría" in caplog.text


def test_publica_las_dos_tablas_junto_al_consolidado(monkeypatch, rutas_del_orquestador):
    """De punta a punta, con el módulo real: los archivos quedan donde mira el BI."""
    import logging
    from typack_etl.audit.publicar import COBERTURA_PUBLICADA_FILENAME, REASIGNACIONES_FILENAME

    resumen = orq.publicar_auditoria(logging.getLogger("t"))

    consolidado = Path(rutas_del_orquestador["consolidated"])
    assert (consolidado / REASIGNACIONES_FILENAME).exists()
    assert (consolidado / COBERTURA_PUBLICADA_FILENAME).exists()
    assert resumen["filas_reasignaciones"] == 0  # sin libro todavía


# --------------------------------------------------------------------------
# Corrida única
# --------------------------------------------------------------------------

def ruta_lock(rutas):
    return Path(rutas["logs"]) / orq.LOCK_FILENAME


def test_el_lock_se_toma_y_se_libera(monkeypatch, rutas_del_orquestador):
    _, codigo = correr_etl(monkeypatch, trans_success=True)

    assert codigo == 0
    assert not ruta_lock(rutas_del_orquestador).exists()


def test_no_corren_dos_etl_a_la_vez(monkeypatch, rutas_del_orquestador):
    """El estado son CSV sin bloqueo: dos escritores simultáneos lo corrompen."""
    ruta_lock(rutas_del_orquestador).write_text("pid=999 inicio=ahora", encoding="utf-8")

    llamadas, codigo = correr_etl(monkeypatch, trans_success=True)

    assert codigo == orq.SALIDA_YA_EN_CURSO
    assert llamadas == []  # ni siquiera empezó
    assert ruta_lock(rutas_del_orquestador).exists()  # no se pisa el lock ajeno


def test_un_lock_vencido_no_bloquea_para_siempre(monkeypatch, rutas_del_orquestador):
    """Si el proceso murió sin limpiar, la corrida siguiente tiene que poder entrar."""
    import os
    import time

    lock = ruta_lock(rutas_del_orquestador)
    lock.write_text("pid=999 inicio=hace mucho", encoding="utf-8")
    viejo = time.time() - (orq.LOCK_ANTIGUEDAD_HORAS + 1) * 3600
    os.utime(lock, (viejo, viejo))

    llamadas, codigo = correr_etl(monkeypatch, trans_success=True)

    assert codigo == 0
    assert len(llamadas) == 1


def test_el_lock_se_libera_aunque_falle_la_corrida(monkeypatch, rutas_del_orquestador):
    import logging

    with pytest.raises(RuntimeError):
        with orq.corrida_unica(logging.getLogger("t")):
            raise RuntimeError("explotó a mitad")

    assert not ruta_lock(rutas_del_orquestador).exists()


# --------------------------------------------------------------------------
# Rotación de las bitácoras del orquestador
# --------------------------------------------------------------------------

def test_rota_los_logs_viejos_del_orquestador(rutas_del_orquestador):
    from datetime import datetime, timedelta

    directorio = Path(rutas_del_orquestador["logs"]) / "scheduler"
    directorio.mkdir(parents=True, exist_ok=True)
    hoy = datetime.now()
    reciente = directorio / f"etl_{hoy:%Y%m%d}.log"
    viejo = directorio / f"etl_{hoy - timedelta(days=120):%Y%m%d}.log"
    raro = directorio / "etl_sin_fecha.log"
    for f in (reciente, viejo, raro):
        f.write_text("x", encoding="utf-8")

    assert orq.rotar_logs_scheduler(90) == 1
    assert reciente.exists()
    assert not viejo.exists()
    assert raro.exists()  # nombre inesperado: no se toca


def test_sin_retencion_no_se_borra_nada(rutas_del_orquestador):
    assert orq.rotar_logs_scheduler(0) == 0


# --------------------------------------------------------------------------
# Reporte semanal
# --------------------------------------------------------------------------

def test_las_lineas_fallidas_llegan_al_tracker(monkeypatch, rutas_del_orquestador):
    """Alimenta el bloque 'líneas sin datos' del reporte semanal."""
    escribir_resultado(rutas_del_orquestador, [
        linea("TF_2"), linea("TF_5", estado="ERROR", rows=0, error="500"),
    ])

    correr_etl(monkeypatch, trans_success=True)

    assert TrackerFalso.registros[-1]["lines_without_data"] == ["TF_5"]


def test_una_alerta_que_no_sale_queda_en_el_log(caplog):
    """Un ETL que falla y no puede avisar era indistinguible de uno sano."""
    import logging

    with caplog.at_level(logging.ERROR):
        assert orq.avisar(logging.getLogger("t"), False) is False
    assert "No se pudo enviar la alerta" in caplog.text

def libro_de_la_semana(audit_dir):
    """Deja en el libro una reasignación detectada hoy, tres días después del evento."""
    from datetime import datetime, timedelta

    from conftest import segmento
    from typack_etl.audit import cdc
    from typack_etl.audit.ledger import append_cambios

    ahora = datetime.now()
    evento = ahora - timedelta(days=3)
    inicio = evento.strftime("%Y-%m-%d %H:%M:%S")
    fin = (evento + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")

    previas = [segmento(inicio, fin, code_description="No asignado", code_t="No asignado")]
    nuevas = [segmento(inicio, fin, code_description="05.- Setup", code_t="Setup")]
    ctx = cdc.Contexto(run_id="r2", devid=1273, linea="TF_8",
                       detectado_en=ahora, run_id_anterior="r1")
    resultado = cdc.comparar(previas, nuevas, ctx,
                             ahora - timedelta(days=35), ahora)
    append_cambios(resultado.cambios, Path(audit_dir))


def test_las_estadisticas_semanales_incluyen_las_reasignaciones(monkeypatch, rutas_del_orquestador):
    from scripts import stats_tracker as st

    monkeypatch.setattr(st, "PATHS", rutas_del_orquestador)
    libro_de_la_semana(rutas_del_orquestador["audit"])

    tracker = st.StatsTracker()
    tracker.record_execution(success=True, rows_extracted=10, rows_transformed=10,
                             duration_seconds=1.0, errors=[])
    stats = tracker.get_weekly_stats()

    assert stats["reasignaciones"]["total"] == 1
    assert stats["reasignaciones"]["por_linea"] == {"TF_8": 1}
    assert stats["reasignaciones"]["retraso_max"] == 3


def enviar_reporte(monkeypatch, stats):
    """Renderiza el reporte semanal capturando el HTML en vez de enviarlo."""
    from scripts import notify

    capturado = {}
    monkeypatch.setitem(notify.EMAIL_CONFIG["recipients"], "weekly_report", ["a@b.cl"])
    monkeypatch.setattr(notify, "_send_email",
                        lambda dest, asunto, html, texto=None: capturado.update(html=html) or True)
    notify.send_weekly_report(stats)
    return capturado["html"]


def stats_base(**extra):
    base = {"week_start": "2026-03-23", "week_end": "2026-03-30",
            "total_executions": 7, "successful_executions": 7, "failed_executions": 0,
            "total_rows_extracted": 100, "total_rows_transformed": 100,
            "lines_without_data": [], "errors": []}
    base.update(extra)
    return base


def test_el_reporte_muestra_las_reasignaciones(monkeypatch):
    html = enviar_reporte(monkeypatch, stats_base(reasignaciones={
        "total": 4, "por_linea": {"TF_8": 3, "TF_2": 1},
        "retraso_mediano": 17.5, "retraso_max": 20,
    }))

    assert "Detenciones reasignadas esta semana: 4" in html
    assert "17.5 días" in html
    assert "TF_8" in html


def test_una_semana_sin_reasignaciones_lo_dice(monkeypatch):
    html = enviar_reporte(monkeypatch, stats_base(reasignaciones={
        "total": 0, "por_linea": {}, "retraso_mediano": None, "retraso_max": None,
    }))
    assert "Ninguna detención cambió de código" in html


def test_sin_libro_legible_el_reporte_se_envia_igual(monkeypatch):
    """Un libro ilegible no puede impedir que salga el reporte semanal."""
    html = enviar_reporte(monkeypatch, stats_base(reasignaciones=None))

    assert "Reporte Semanal ETL Typack" in html
    assert "reasignadas" not in html


# --------------------------------------------------------------------------
# Conteo del consolidado
# --------------------------------------------------------------------------

def test_cuenta_las_filas_del_consolidado(rutas_del_orquestador):
    destino = Path(rutas_del_orquestador["consolidated"]) / "mt_typ_consolidated.csv"
    destino.write_text("a;b\n1;2\n3;4\n5;6\n", encoding="utf-8-sig")
    assert orq.contar_consolidado() == 3


def test_sin_consolidado_cuenta_cero(rutas_del_orquestador):
    assert orq.contar_consolidado() == 0
