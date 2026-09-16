"""
Las dos garantías de la suite: no toca producción y no toca la red.

Estos tests verifican los propios fixtures. Existen porque un descuido acá no
se nota hasta que ya se sobrescribieron datos reales en Drive.
"""

from pathlib import Path

import pytest
import requests

from config import settings
from typack_etl.extractor.pipelines import extractor_30m
from typack_etl.transformer.transformer import DataTransformer


def test_las_rutas_apuntan_a_temporal(tmp_path):
    for clave, ruta in settings.PATHS.items():
        assert str(tmp_path) in str(ruta), f"PATHS[{clave!r}] no está aislada: {ruta}"


def test_estan_aisladas_TODAS_las_rutas_de_datos(tmp_path):
    """Una ruta nueva sin aislar escribiría en Drive desde el primer test."""
    esperadas = {"raw", "processed", "consolidated", "stream_data", "logs", "audit", "snapshots"}
    assert esperadas <= set(settings.PATHS), f"faltan rutas: {esperadas - set(settings.PATHS)}"


def test_ningun_modulo_conserva_las_rutas_de_produccion(tmp_path):
    """Los módulos hacen `from config.settings import PATHS` y guardan su copia."""
    for modulo in (extractor_30m, __import__(
            "typack_etl.transformer.transformer", fromlist=["x"])):
        for clave, ruta in modulo.PATHS.items():
            assert str(tmp_path) in str(ruta), f"{modulo.__name__}.PATHS[{clave!r}] = {ruta}"


def test_un_transformer_por_defecto_escribe_en_temporal(tmp_path):
    """Aunque no se le pase ninguna ruta."""
    t = DataTransformer()
    assert str(tmp_path) in t.base_path
    assert str(tmp_path) in t.processed_path
    assert str(tmp_path) in t.consolidated_path


def test_la_red_esta_bloqueada():
    with pytest.raises(RuntimeError, match="no puede salir a la red"):
        requests.get("https://mts2.mtsolutions.io/api/login", timeout=1)


def test_el_extractor_sin_simular_la_api_falla_en_vez_de_llamarla():
    with pytest.raises(RuntimeError, match="no puede salir a la red"):
        extractor_30m.get_token()


def test_las_rutas_de_produccion_no_aparecen_en_ningun_lado(tmp_path):
    """Ninguna ruta activa debe apuntar a la unidad compartida."""
    sospechosas = [str(r) for r in settings.PATHS.values() if "Unidades compartidas" in str(r)]
    assert sospechosas == []
