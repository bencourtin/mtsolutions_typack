"""Publicación de la auditoría junto al consolidado."""

from datetime import datetime

import pytest

from conftest import leer_csv, segmento
from typack_etl.audit import cdc, publicar
from typack_etl.audit.keys import record_key
from typack_etl.audit.ledger import (
    LEDGER_FILENAME,
    append_cambios,
    read_ledger,
    reasignaciones_recientes,
    registrar_cobertura,
)
from typack_etl.audit.publicar import (
    COBERTURA_PUBLICADA_COLUMNS,
    COBERTURA_PUBLICADA_FILENAME,
    REASIGNACIONES_COLUMNS,
    REASIGNACIONES_FILENAME,
)


def dt(texto):
    return datetime.strptime(texto, "%Y-%m-%d %H:%M:%S")


VENTANA = (dt("2026-03-01 00:00:00"), dt("2026-03-31 23:59:59"))
DEVID = 1273


def ctx(detectado_en, run_id="r2", anterior="r1"):
    return cdc.Contexto(run_id=run_id, devid=DEVID, linea="TF_8",
                        detectado_en=dt(detectado_en), run_id_anterior=anterior)


def sabana(**campos):
    """Un segmento de una hora, con los códigos que se le pasen."""
    base = dict(code_description="No asignado", code_t="No asignado")
    base.update(campos)
    return segmento("2026-03-10 08:00:00", "2026-03-10 09:00:00", **base)


def registrar(audit_dir, previas, nuevas, detectado_en):
    """Corre el CDC y vuelca lo detectado al libro, como haría una corrida."""
    resultado = cdc.comparar(previas, nuevas, ctx(detectado_en), *VENTANA)
    append_cambios(resultado.cambios, audit_dir)
    return resultado


def read_libro(audit_dir):
    """Las filas crudas del libro, para contrastarlas con lo publicado."""
    return read_ledger(audit_dir / LEDGER_FILENAME)


@pytest.fixture
def libro_con_una_reasignacion(tmp_path):
    """Un libro con una única reclasificación: 'No asignado' -> 'Setup'."""
    previas = [sabana()]
    nuevas = [sabana(code_description="05.- Setup", code_t="Setup")]
    registrar(tmp_path, previas, nuevas, "2026-03-25 07:00:00")
    return tmp_path


# --------------------------------------------------------------------------
# Qué se publica
# --------------------------------------------------------------------------

def test_la_descripcion_se_pliega_sobre_el_codigo(libro_con_una_reasignacion):
    """El libro escribe code_t y code_description; se publica una sola fila."""
    filas = publicar.reasignaciones(libro_con_una_reasignacion)

    assert len(filas) == 1
    fila = filas[0]
    assert fila["Campo"] == "code_t"
    assert (fila["Cod_Anterior"], fila["Cod_Nuevo"]) == ("No asignado", "Setup")
    assert (fila["Desc_Anterior"], fila["Desc_Nuevo"]) == ("No asignado", "05.- Setup")


def test_solo_se_publican_las_reasignaciones(tmp_path):
    """Un comentario editado y un segmento perdido no son preguntas de negocio."""
    previas = [sabana(), segmento("2026-03-11 08:00:00", "2026-03-11 09:00:00")]
    nuevas = [sabana(comments="revisar")]  # editado, y el segundo desaparece
    resultado = registrar(tmp_path, previas, nuevas, "2026-03-25 07:00:00")

    tipos = {c.tipo_cambio for c in resultado.cambios}
    assert cdc.OTRO_CAMBIO in tipos and cdc.DESAPARECIDO in tipos
    assert publicar.reasignaciones(tmp_path) == []


def test_los_campos_de_contexto_no_generan_fila_propia(tmp_path):
    """Si en la misma reclasificación se editó el comentario, no se duplica."""
    previas = [sabana()]
    nuevas = [sabana(code_description="05.- Setup", code_t="Setup", comments="ajuste")]
    registrar(tmp_path, previas, nuevas, "2026-03-25 07:00:00")

    filas = publicar.reasignaciones(tmp_path)
    assert len(filas) == 1
    assert filas[0]["Campo"] == "code_t"


# --------------------------------------------------------------------------
# Deduplicación de re-detecciones
# --------------------------------------------------------------------------

def test_la_redeteccion_del_mismo_cambio_no_duplica(tmp_path):
    """Seis corridas contra un archivo que no avanzó dejan una sola fila.

    Es el caso real del libro de `data/audit`: `--output` y `--audit-dir` son
    independientes, así que el lado 'anterior' nunca cambió y la misma
    reasignación quedó registrada seis veces.
    """
    previas = [sabana()]
    nuevas = [sabana(code_description="05.- Setup", code_t="Setup")]
    for minuto in range(6):
        registrar(tmp_path, previas, nuevas, f"2026-03-25 07:0{minuto}:00")

    filas = publicar.reasignaciones(tmp_path)
    assert len(filas) == 1
    assert filas[0]["Detectado_En"] == "2026-03-25 07:00:00"  # la más antigua


def test_dos_reclasificaciones_genuinas_no_se_colapsan(tmp_path):
    """Un segundo cambio real encadena valores distintos, así que sobrevive."""
    v0 = [sabana()]
    v1 = [sabana(code_description="05.- Setup", code_t="Setup")]
    v2 = [sabana(code_description="10.- Falla", code_t="Falla")]
    registrar(tmp_path, v0, v1, "2026-03-25 07:00:00")
    registrar(tmp_path, v1, v2, "2026-03-26 07:00:00")

    filas = publicar.reasignaciones(tmp_path)
    assert len(filas) == 2
    assert [(f["Cod_Anterior"], f["Cod_Nuevo"]) for f in filas] == [
        ("No asignado", "Setup"),
        ("Setup", "Falla"),
    ]


# --------------------------------------------------------------------------
# Unión con el consolidado
# --------------------------------------------------------------------------

def test_la_clave_publicada_une_con_el_consolidado(libro_con_una_reasignacion):
    """`Record_Key` es la misma clave que el extractor estampa en la fila."""
    fila = publicar.reasignaciones(libro_con_una_reasignacion)[0]
    esperada = record_key(DEVID, sabana())

    assert fila["Record_Key"] == esperada


def test_el_retraso_de_asignacion_viaja_en_la_fila(libro_con_una_reasignacion):
    """`Dias_Desde_Evento` es la medida que mide cuánto tardó la planta."""
    fila = publicar.reasignaciones(libro_con_una_reasignacion)[0]
    assert int(fila["Dias_Desde_Evento"]) == 15  # 10/03 -> 25/03


# --------------------------------------------------------------------------
# Escritura
# --------------------------------------------------------------------------

def test_publicar_escribe_las_dos_tablas(tmp_path, libro_con_una_reasignacion):
    destino = tmp_path / "consolidated"
    registrar_cobertura("TF_8", DEVID, {"2026-03-10": 1440.0},
                        libro_con_una_reasignacion, "r2", dt("2026-03-25 07:00:00"))

    resumen = publicar.publicar(libro_con_una_reasignacion, destino)

    assert (destino / REASIGNACIONES_FILENAME).exists()
    assert (destino / COBERTURA_PUBLICADA_FILENAME).exists()
    assert resumen["filas_reasignaciones"] == 1
    assert resumen["filas_cobertura"] == 1


def test_sin_libro_se_publica_solo_la_cabecera(tmp_path):
    """Un archivo ausente rompe el modelo del BI en cada refresco; uno vacío no."""
    destino = tmp_path / "consolidated"
    resumen = publicar.publicar(tmp_path / "audit_inexistente", destino)

    assert resumen["filas_reasignaciones"] == 0
    assert leer_csv(destino / REASIGNACIONES_FILENAME) == []
    cabecera = (destino / REASIGNACIONES_FILENAME).read_text(encoding="utf-8-sig")
    assert cabecera.strip() == ";".join(REASIGNACIONES_COLUMNS)


def test_las_columnas_siguen_la_convencion_del_consolidado(tmp_path, libro_con_una_reasignacion):
    destino = tmp_path / "consolidated"
    publicar.publicar(libro_con_una_reasignacion, destino)

    filas = leer_csv(destino / REASIGNACIONES_FILENAME)
    assert list(filas[0].keys()) == list(REASIGNACIONES_COLUMNS)


def test_la_cobertura_se_republica_con_nombres_del_bi(tmp_path, libro_con_una_reasignacion):
    destino = tmp_path / "consolidated"
    registrar_cobertura("TF_8", DEVID, {"2026-03-10": 1200.0},
                        libro_con_una_reasignacion, "r2", dt("2026-03-25 07:00:00"))
    publicar.publicar(libro_con_una_reasignacion, destino)

    filas = leer_csv(destino / COBERTURA_PUBLICADA_FILENAME)
    assert list(filas[0].keys()) == list(COBERTURA_PUBLICADA_COLUMNS)
    assert filas[0]["Linea"] == "TF_8"


def test_la_escritura_no_deja_temporales(tmp_path, libro_con_una_reasignacion):
    destino = tmp_path / "consolidated"
    publicar.publicar(libro_con_una_reasignacion, destino)

    assert list(destino.glob("*.tmp")) == []


def test_republicar_no_acumula(tmp_path, libro_con_una_reasignacion):
    """Se reemplaza, no se agrega: el destino no es append-only."""
    destino = tmp_path / "consolidated"
    publicar.publicar(libro_con_una_reasignacion, destino)
    publicar.publicar(libro_con_una_reasignacion, destino)

    assert len(leer_csv(destino / REASIGNACIONES_FILENAME)) == 1


# --------------------------------------------------------------------------
# Resumen del reporte semanal
# --------------------------------------------------------------------------

SEMANA = ("2026-03-23", "2026-03-30")


def test_el_resumen_cuenta_eventos_y_no_filas_del_libro(tmp_path):
    """Dos campos por reclasificación y seis re-detecciones son un solo evento."""
    previas = [sabana()]
    nuevas = [sabana(code_description="05.- Setup", code_t="Setup")]
    for minuto in range(6):
        registrar(tmp_path, previas, nuevas, f"2026-03-25 07:0{minuto}:00")

    assert len(read_libro(tmp_path)) == 12  # 6 corridas x 2 campos
    assert publicar.resumen(tmp_path, *SEMANA)["total"] == 1


def test_el_resumen_ventana_por_deteccion(tmp_path):
    """Lo detectado fuera de la semana no entra, aunque siga en el libro."""
    previas = [sabana()]
    dentro = [sabana(code_description="05.- Setup", code_t="Setup")]
    fuera = [sabana(code_description="10.- Falla", code_t="Falla")]
    registrar(tmp_path, previas, dentro, "2026-03-25 07:00:00")
    registrar(tmp_path, previas, fuera, "2026-02-10 07:00:00")

    resumen = publicar.resumen(tmp_path, *SEMANA)
    assert resumen["total"] == 1
    assert publicar.reasignaciones(tmp_path) != []  # el libro conserva las dos


def test_el_resumen_no_usa_el_filtro_por_antiguedad_del_evento(tmp_path):
    """Un cambio detectado meses después del evento igual cuenta esta semana.

    `ledger.reasignaciones_recientes` filtra por `dias_desde_evento`, no por
    cuándo se detectó: sobre el libro real —cuyos cambios aparecieron 148 días
    después del evento— devuelve cero. El resumen semanal no puede heredar ese
    filtro o informaría "ninguna reasignación" cada semana.
    """
    previas = [segmento("2025-10-01 08:00:00", "2025-10-01 09:00:00",
                        code_description="No asignado", code_t="No asignado")]
    nuevas = [segmento("2025-10-01 08:00:00", "2025-10-01 09:00:00",
                       code_description="05.- Setup", code_t="Setup")]
    ventana_vieja = (dt("2025-09-01 00:00:00"), dt("2025-12-31 23:59:59"))
    resultado = cdc.comparar(previas, nuevas, ctx("2026-03-25 07:00:00"), *ventana_vieja)
    append_cambios(resultado.cambios, tmp_path)

    assert reasignaciones_recientes(tmp_path, dias=30) == []
    assert publicar.resumen(tmp_path, *SEMANA)["total"] == 1


def test_el_resumen_reporta_el_retraso_y_las_lineas(tmp_path):
    previas = [sabana()]
    nuevas = [sabana(code_description="05.- Setup", code_t="Setup")]
    registrar(tmp_path, previas, nuevas, "2026-03-25 07:00:00")

    resumen = publicar.resumen(tmp_path, *SEMANA)
    assert resumen["por_linea"] == {"TF_8": 1}
    assert resumen["retraso_mediano"] == 15.0
    assert resumen["retraso_max"] == 15


def test_sin_libro_el_resumen_es_cero(tmp_path):
    resumen = publicar.resumen(tmp_path, *SEMANA)
    assert resumen["total"] == 0
    assert resumen["retraso_mediano"] is None
