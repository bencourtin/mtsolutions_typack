"""Recarga histórica: rango operativo, reanudabilidad y guardas del destino."""

import csv
from pathlib import Path
from types import SimpleNamespace

import pytest

from datetime import date

from scripts import recarga_historica as rec
from typack_etl.extractor.utils.date_utils import corte_utc_esperado


def corte_txt(dia):
    """El corte que le corresponde a `dia`.

    Se calcula por fecha y no se lee de la configuracion: el corte se mueve
    dos veces al ano, y un mes de invierno no arranca a la misma hora UTC que
    uno de verano.
    """
    return corte_utc_esperado(dia).strftime("%H:%M:%S")

COLUMNAS_RESULTS = ["line", "devid", "start", "end", "rows",
                    "success", "estado", "cobertura_pct", "error", "detalle"]


def escribir_results(logs_dir, filas, stamp="20260828_120000"):
    """Deja un results_*.csv como el que produce el extractor."""
    logs_dir = Path(logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)
    destino = logs_dir / f"results_{stamp}.csv"
    with destino.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNAS_RESULTS, delimiter=";")
        w.writeheader()
        for fila in filas:
            w.writerow({c: fila.get(c, "") for c in COLUMNAS_RESULTS})
    return destino


def linea(nombre, estado="OK", rows=100, error=""):
    return {"line": nombre, "devid": "1", "rows": rows, "estado": estado,
            "success": "0" if estado == "ERROR" else "1",
            "cobertura_pct": "100.0", "error": error}


@pytest.fixture
def extractor_falso(monkeypatch):
    """Reemplaza al extractor por uno que sólo deja su bitácora.

    Devuelve la lista de comandos invocados, para poder inspeccionarlos.
    """
    llamadas = []

    def instalar(filas=None, returncode=0):
        filas = filas if filas is not None else [linea("TF_2"), linea("TF_3")]

        def falso(cmd, **kw):
            llamadas.append(cmd)
            logs_dir = Path(cmd[cmd.index("--logs-dir") + 1])
            if returncode == 0:
                escribir_results(logs_dir, filas)
            return SimpleNamespace(returncode=returncode, stdout="", stderr="")

        monkeypatch.setattr(rec.subprocess, "run", falso)
        return llamadas

    return instalar


def correr(tmp_path, *extra):
    """Invoca main() con salida y bitácoras siempre en el temporal."""
    return rec.main([
        "--output", str(tmp_path / "raw"),
        "--logs-dir", str(tmp_path / "logs"),
        *extra,
    ])


# --------------------------------------------------------------------------
# Rango de meses
# --------------------------------------------------------------------------

def test_meses_incluye_ambos_extremos_y_cruza_el_año():
    assert rec.meses((2024, 11), (2025, 2)) == [(2024, 11), (2024, 12), (2025, 1), (2025, 2)]


def test_un_rango_invertido_no_devuelve_nada():
    assert rec.meses((2025, 3), (2025, 1)) == []


def test_el_rango_usa_el_corte_operativo_no_la_medianoche():
    """El .bat viejo usaba 00:00 y dejaba afuera el turno de noche del día 1."""
    assert rec.rango_utc(2024, 5) == ("2024-05-01 12:00:00", "2024-06-01 12:00:00")  # invierno


def test_el_rango_de_diciembre_cierra_en_enero():
    assert rec.rango_utc(2024, 12) == ("2024-12-01 11:00:00", "2025-01-01 11:00:00")  # verano


def test_el_default_arranca_en_el_mes_mas_antiguo_de_produccion():
    assert rec.MES_MAS_ANTIGUO == (2024, 5)


# --------------------------------------------------------------------------
# El comando que se le pasa al extractor
# --------------------------------------------------------------------------

def test_la_auditoria_queda_encendida(tmp_path):
    """Con --no-audit, `traza.anotar()` no corre y los CSV salen sin record_key."""
    cmd = rec.comando_del_mes(2025, 3, tmp_path / "raw",
                              tmp_path / "logs", tmp_path / "audit")

    assert "--no-audit" not in cmd
    assert "--audit-dir" in cmd
    assert cmd[cmd.index("--audit-dir") + 1] == str(tmp_path / "audit")


def test_no_se_guardan_snapshots_de_la_historia(tmp_path):
    cmd = rec.comando_del_mes(2025, 3, tmp_path / "raw", tmp_path / "logs", tmp_path / "audit")
    assert "--no-snapshots" in cmd and "--no-progress" in cmd


def test_el_libro_desechable_no_es_el_de_produccion(tmp_path, extractor_falso):
    """El libro real tiene que nacer limpio en la primera corrida diaria."""
    llamadas = extractor_falso()
    correr(tmp_path, "--desde", "2025-01", "--hasta", "2025-01")

    audit = Path(llamadas[0][llamadas[0].index("--audit-dir") + 1])
    assert "recarga" in audit.parts


# --------------------------------------------------------------------------
# Guarda del destino sucio
# --------------------------------------------------------------------------

def ensuciar(tmp_path, year=2025, month=1):
    destino = tmp_path / "raw" / str(year) / f"{month:02d}"
    destino.mkdir(parents=True, exist_ok=True)
    archivo = destino / f"produccion_{year}_enero_TF_2.csv"
    archivo.write_text("start;end\n", encoding="utf-8-sig")
    return archivo


def test_no_arranca_sobre_un_destino_sucio(tmp_path, extractor_falso, capsys):
    """Recargar encima marcaría reasignada=1 en miles de filas por formato."""
    llamadas = extractor_falso()
    ensuciar(tmp_path)

    codigo = correr(tmp_path, "--desde", "2025-01", "--hasta", "2025-01")

    assert codigo == rec.SALIDA_DESTINO_SUCIO
    assert llamadas == []  # no llamó al extractor
    assert "no está vacío" in capsys.readouterr().out


def test_el_destino_sucio_se_puede_forzar(tmp_path, extractor_falso):
    llamadas = extractor_falso()
    ensuciar(tmp_path)

    codigo = correr(tmp_path, "--desde", "2025-01", "--hasta", "2025-01",
                    "--permitir-destino-sucio")

    assert codigo == rec.SALIDA_OK
    assert len(llamadas) == 1


def test_un_destino_limpio_no_molesta(tmp_path, extractor_falso):
    llamadas = extractor_falso()
    assert correr(tmp_path, "--desde", "2025-01", "--hasta", "2025-02") == rec.SALIDA_OK
    assert len(llamadas) == 2


# --------------------------------------------------------------------------
# Reanudabilidad
# --------------------------------------------------------------------------

def test_saltea_los_meses_que_ya_cerraron_bien(tmp_path, extractor_falso):
    """Un corte a mitad de camino no obliga a empezar de nuevo."""
    llamadas = extractor_falso()
    escribir_results(rec.logs_del_mes(tmp_path / "logs", 2025, 1), [linea("TF_2")])

    correr(tmp_path, "--desde", "2025-01", "--hasta", "2025-02")

    assert len(llamadas) == 1  # sólo febrero
    assert f"2025-02-01 {corte_txt(date(2025, 2, 1))}" in llamadas[0]


def test_rehacer_no_saltea_nada(tmp_path, extractor_falso):
    llamadas = extractor_falso()
    escribir_results(rec.logs_del_mes(tmp_path / "logs", 2025, 1), [linea("TF_2")])

    correr(tmp_path, "--desde", "2025-01", "--hasta", "2025-02", "--rehacer")

    assert len(llamadas) == 2


def test_un_mes_con_demasiadas_fallas_no_cuenta_como_hecho(tmp_path, extractor_falso):
    """Si quedó a medias, la corrida siguiente tiene que volver a intentarlo."""
    fallidas = [linea(f"EXT_{i}", estado="ERROR", rows=0, error="500")
                for i in range(rec.ETL_MAX_LINEAS_FALLIDAS + 1)]
    escribir_results(rec.logs_del_mes(tmp_path / "logs", 2025, 1), fallidas)

    llamadas = extractor_falso()
    correr(tmp_path, "--desde", "2025-01", "--hasta", "2025-01")

    assert len(llamadas) == 1


# --------------------------------------------------------------------------
# Manejo de fallos
# --------------------------------------------------------------------------

def test_aborta_al_primer_mes_fallido(tmp_path, extractor_falso):
    llamadas = extractor_falso(returncode=1)

    codigo = correr(tmp_path, "--desde", "2025-01", "--hasta", "2025-04")

    assert codigo == rec.SALIDA_FALLO
    assert len(llamadas) == 1  # no siguió con febrero


def test_continuar_con_errores_recorre_todo(tmp_path, extractor_falso):
    llamadas = extractor_falso(returncode=1)

    codigo = correr(tmp_path, "--desde", "2025-01", "--hasta", "2025-03",
                    "--continuar-con-errores")

    assert codigo == rec.SALIDA_FALLO
    assert len(llamadas) == 3


def test_un_mes_sin_bitacora_es_un_fallo(tmp_path, monkeypatch):
    """Sin results_*.csv no hay forma de verificar qué pasó."""
    monkeypatch.setattr(rec.subprocess, "run",
                        lambda cmd, **kw: SimpleNamespace(returncode=0, stdout="", stderr=""))

    assert correr(tmp_path, "--desde", "2025-01", "--hasta", "2025-01") == rec.SALIDA_FALLO


# --------------------------------------------------------------------------
# Dry run
# --------------------------------------------------------------------------

def test_dry_run_no_llama_al_extractor(tmp_path, extractor_falso, capsys):
    llamadas = extractor_falso()

    codigo = correr(tmp_path, "--desde", "2025-01", "--hasta", "2025-03", "--dry-run")

    assert codigo == rec.SALIDA_OK
    assert llamadas == []
    salida = capsys.readouterr().out
    assert "Recargaría 3 mes(es)" in salida
    assert f"2025-01-01 {corte_txt(date(2025, 1, 1))}" in salida


def test_dry_run_avisa_si_el_destino_esta_sucio(tmp_path, capsys):
    ensuciar(tmp_path)
    correr(tmp_path, "--desde", "2025-01", "--hasta", "2025-01", "--dry-run")

    assert "ATENCIÓN" in capsys.readouterr().out
