"""Landing inmutable: snapshots crudos, manifiesto y retención (fase 2)."""

import gzip
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from conftest import leer_csv, segmento
from typack_etl.extractor.functions.landing import (
    MANIFEST_FILENAME,
    append_manifest,
    buscar_en_manifest,
    escribir_snapshot,
    leer_manifest,
    leer_snapshot,
    rotar_snapshots,
    ruta_snapshot,
)
from typack_etl.extractor.pipelines import extractor_30m
from typack_etl.extractor.utils.date_utils import now_planta


SOBRE = {
    "run_id": "20260827_120000_abcd1234",
    "extracted_at": "2026-08-27 12:00:00",
    "devid": 1273,
    "linea": "TF_8",
    "window_start": "2026-07-23 12:00:00",
    "window_end": "2026-08-27 12:00:00",
    "http_status": 200,
}


# --------------------------------------------------------------------------
# Snapshots
# --------------------------------------------------------------------------

def test_la_ruta_se_particiona_por_dia(tmp_path):
    destino = ruta_snapshot(tmp_path, "run-1", 1273, datetime(2026, 8, 27, 15, 0))
    assert destino.parts[-4:] == ("2026", "08", "27", "run-1_1273.jsonl.gz")


def test_guarda_la_respuesta_tal_cual_llego(tmp_path):
    filas = [segmento("2026-08-01 08:00:00", "2026-08-01 18:00:00"),
             segmento("2026-08-01 18:00:00", "2026-08-01 22:00:00")]
    destino = escribir_snapshot(tmp_path / "s.jsonl.gz", SOBRE, filas)

    sobre, registros = leer_snapshot(destino)
    assert registros == filas, "los registros deben volver idénticos"
    assert sobre["run_id"] == SOBRE["run_id"]
    assert sobre["forma"] == "lista"
    assert sobre["n_registros"] == 2


def test_queda_comprimido(tmp_path):
    filas = [segmento(f"2026-08-01 {h:02d}:00:00", f"2026-08-01 {h+1:02d}:00:00") for h in range(20)]
    destino = escribir_snapshot(tmp_path / "s.jsonl.gz", SOBRE, filas)

    crudo = len(json.dumps(filas).encode("utf-8"))
    assert destino.stat().st_size < crudo / 2
    with gzip.open(destino, "rt", encoding="utf-8") as f:
        assert f.readline().startswith("{"), "la primera línea es el sobre"


def test_una_respuesta_que_no_es_lista_se_guarda_entera(tmp_path):
    envoltorio = {"data": [{"a": 1}], "meta": {"total": 1}}
    destino = escribir_snapshot(tmp_path / "s.jsonl.gz", SOBRE, envoltorio)

    sobre, registros = leer_snapshot(destino)
    assert sobre["forma"] == "objeto"
    assert registros == [envoltorio]


def test_una_respuesta_vacia_deja_constancia(tmp_path):
    destino = escribir_snapshot(tmp_path / "s.jsonl.gz", {**SOBRE, "error": "500"}, None)
    sobre, registros = leer_snapshot(destino)
    assert sobre["forma"] == "vacio"
    assert sobre["error"] == "500"
    assert registros == []


def test_un_snapshot_nunca_se_sobrescribe(tmp_path):
    destino = tmp_path / "s.jsonl.gz"
    escribir_snapshot(destino, SOBRE, [{"a": 1}])
    with pytest.raises(FileExistsError):
        escribir_snapshot(destino, SOBRE, [{"a": 2}])

    _, registros = leer_snapshot(destino)
    assert registros == [{"a": 1}], "el original queda intacto"


def test_un_fallo_a_media_escritura_no_deja_basura(tmp_path, monkeypatch):
    destino = tmp_path / "s.jsonl.gz"

    def revienta(*a, **kw):
        raise RuntimeError("disco lleno")

    monkeypatch.setattr("typack_etl.extractor.functions.landing.os.replace", revienta)
    with pytest.raises(RuntimeError):
        escribir_snapshot(destino, SOBRE, [{"a": 1}])

    assert not destino.exists()
    assert not (tmp_path / "s.jsonl.gz.tmp").exists()


def test_los_acentos_sobreviven(tmp_path):
    fila = segmento("2026-08-01 08:00:00", "2026-08-01 09:00:00",
                    code_description="16.- Período no laboral", user="JOHAN NIÑO")
    destino = escribir_snapshot(tmp_path / "s.jsonl.gz", SOBRE, [fila])
    _, registros = leer_snapshot(destino)
    assert registros[0]["code_description"] == "16.- Período no laboral"
    assert registros[0]["user"] == "JOHAN NIÑO"


# --------------------------------------------------------------------------
# Manifiesto
# --------------------------------------------------------------------------

def test_el_manifiesto_es_append_only(tmp_path):
    append_manifest([{**SOBRE, "estado": "OK"}], tmp_path)
    append_manifest([{**SOBRE, "estado": "SIN_DATOS", "linea": "TF_9"}], tmp_path)

    entradas = leer_manifest(tmp_path)
    assert len(entradas) == 2
    assert [e["linea"] for e in entradas] == ["TF_8", "TF_9"]


def test_sin_entradas_no_escribe_nada(tmp_path):
    append_manifest([], tmp_path)
    assert leer_manifest(tmp_path) == []


def test_una_linea_corrupta_no_invalida_el_indice(tmp_path):
    append_manifest([{**SOBRE, "estado": "OK"}], tmp_path)
    with (tmp_path / MANIFEST_FILENAME).open("a", encoding="utf-8") as f:
        f.write("{esto no es json\n")
    append_manifest([{**SOBRE, "estado": "ERROR", "linea": "TF_9"}], tmp_path)

    entradas = leer_manifest(tmp_path)
    assert len(entradas) == 2


def test_se_puede_preguntar_que_paso_con_una_linea(tmp_path):
    append_manifest([
        {**SOBRE, "estado": "OK"},
        {**SOBRE, "estado": "ERROR", "linea": "TF_9", "error": "timeout"},
        {**SOBRE, "estado": "OK", "linea": "TF_9", "run_id": "otro"},
    ], tmp_path)

    assert len(buscar_en_manifest(tmp_path, linea="TF_9")) == 2
    assert len(buscar_en_manifest(tmp_path, estado="ERROR")) == 1
    assert len(buscar_en_manifest(tmp_path, run_id="otro")) == 1


# --------------------------------------------------------------------------
# Retención
# --------------------------------------------------------------------------

def _sembrar(base, dias_atras, devid=1273):
    momento = now_planta() - timedelta(days=dias_atras)
    destino = ruta_snapshot(base, f"run-{dias_atras}", devid, momento)
    return escribir_snapshot(destino, SOBRE, [{"a": dias_atras}])


def test_purga_lo_viejo_y_conserva_lo_reciente(tmp_path):
    viejos = [_sembrar(tmp_path, d) for d in (200, 120, 91)]
    nuevos = [_sembrar(tmp_path, d) for d in (89, 10, 0)]

    assert rotar_snapshots(tmp_path, 90) == 3
    assert not any(p.exists() for p in viejos)
    assert all(p.exists() for p in nuevos)


def test_las_carpetas_vacias_se_limpian(tmp_path):
    viejo = _sembrar(tmp_path, 200)
    rotar_snapshots(tmp_path, 90)
    assert not viejo.parent.exists(), "el directorio del día debe desaparecer"


def test_el_manifiesto_sobrevive_a_la_purga(tmp_path):
    _sembrar(tmp_path, 200)
    append_manifest([{**SOBRE, "estado": "OK"}], tmp_path)

    rotar_snapshots(tmp_path, 90)
    assert len(leer_manifest(tmp_path)) == 1, "el índice dice qué hubo aunque el contenido ya no esté"


def test_retencion_cero_desactiva_la_purga(tmp_path):
    viejo = _sembrar(tmp_path, 500)
    assert rotar_snapshots(tmp_path, 0) == 0
    assert viejo.exists()


def test_un_directorio_inexistente_no_rompe(tmp_path):
    assert rotar_snapshots(tmp_path / "no_existe", 90) == 0


def test_una_estructura_inesperada_no_se_toca(tmp_path):
    raro = tmp_path / "backup" / "manual" / "cosas"
    raro.mkdir(parents=True)
    (raro / "algo.jsonl.gz").write_bytes(b"x")
    rotar_snapshots(tmp_path, 1)
    assert (raro / "algo.jsonl.gz").exists()


# --------------------------------------------------------------------------
# Integración con el pipeline
# --------------------------------------------------------------------------

@pytest.fixture
def rutas(tmp_path):
    return {k: tmp_path / k for k in ("raw", "logs", "audit", "snapshots")}


def correr(rutas, api_falsa, respuesta, extra=()):
    api_falsa(respuesta)
    return extractor_30m.main([
        "--start-date", "2026-03-25 12:00:00", "--end-date", "2026-04-05 12:00:00",
        "--devid", "1273",
        "--output", str(rutas["raw"]), "--logs-dir", str(rutas["logs"]),
        "--audit-dir", str(rutas["audit"]), "--snapshots-dir", str(rutas["snapshots"]),
        "--no-progress", *extra,
    ])


FIXTURE = [segmento("2026-03-30 08:00:00", "2026-03-30 18:00:00"),
           segmento("2026-04-01 08:00:00", "2026-04-01 18:00:00")]


def test_la_corrida_deja_el_crudo_y_el_manifiesto(rutas, api_falsa):
    correr(rutas, api_falsa, FIXTURE)

    snapshots = list(rutas["snapshots"].glob("*/*/*/*.jsonl.gz"))
    assert len(snapshots) == 1

    sobre, registros = leer_snapshot(snapshots[0])
    assert registros == FIXTURE, "el crudo debe ser idéntico a lo que devolvió la API"
    assert sobre["linea"] == "TF_8"
    assert sobre["window_start"] == "2026-03-25 12:00:00"
    assert sobre["http_status"] == 200

    entradas = leer_manifest(rutas["snapshots"])
    assert len(entradas) == 1
    assert entradas[0]["estado"] == "OK"
    assert entradas[0]["n_filas"] == 2
    assert Path(entradas[0]["snapshot"]) == snapshots[0]


def test_el_crudo_es_anterior_a_la_normalizacion(rutas, api_falsa):
    """La API omite las claves nulas; el CSV las rellena. El crudo, no."""
    parcial = [{"start": "2026-03-30 08:00:00", "end": "2026-03-30 18:00:00", "duration": 600.0}]
    correr(rutas, api_falsa, parcial)

    _, registros = leer_snapshot(list(rutas["snapshots"].glob("*/*/*/*.jsonl.gz"))[0])
    assert registros == parcial
    assert "code_description" not in registros[0], "el crudo no inventa columnas"
    assert registros[0]["duration"] == 600.0, "ni convierte el decimal a coma"


def test_dos_corridas_dejan_dos_snapshots(rutas, api_falsa):
    correr(rutas, api_falsa, FIXTURE)
    correr(rutas, api_falsa, FIXTURE)

    assert len(list(rutas["snapshots"].glob("*/*/*/*.jsonl.gz"))) == 2, "no se pisan entre sí"
    assert len(leer_manifest(rutas["snapshots"])) == 2


def test_una_linea_que_falla_igual_deja_constancia(rutas, api_falsa):
    def revienta(token, start, end, devid=None):
        raise RuntimeError("500 desde la API")

    correr(rutas, api_falsa, revienta)

    sobre, registros = leer_snapshot(list(rutas["snapshots"].glob("*/*/*/*.jsonl.gz"))[0])
    assert registros == []
    assert "500 desde la API" in sobre["error"]

    entrada = leer_manifest(rutas["snapshots"])[0]
    assert entrada["estado"] == "ERROR"
    assert entrada["n_filas"] == 0


def test_no_snapshots_desactiva_el_landing(rutas, api_falsa):
    assert correr(rutas, api_falsa, FIXTURE, ["--no-snapshots"]) == 0
    assert list(rutas["snapshots"].glob("*/*/*/*.jsonl.gz")) == []
    assert leer_manifest(rutas["snapshots"]) == []


def test_el_manifiesto_permite_reconstruir_la_historia(rutas, api_falsa):
    """El caso de uso: qué decía este dato en cada corrida."""
    correr(rutas, api_falsa, FIXTURE)
    reasignado = [dict(FIXTURE[0], code_description="07.- Matricería")] + FIXTURE[1:]
    correr(rutas, api_falsa, reasignado)

    historia = buscar_en_manifest(rutas["snapshots"], linea="TF_8")
    assert len(historia) == 2

    codigos = []
    for entrada in historia:
        _, registros = leer_snapshot(entrada["snapshot"])
        codigos.append(registros[0]["code_description"])
    assert codigos == ["", "07.- Matricería"]
