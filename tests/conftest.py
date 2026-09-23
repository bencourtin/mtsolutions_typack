"""
Configuración común de la suite.

Regla número uno: **ningún test escribe en producción.** Las rutas de `.env`
apuntan a Google Drive, y un test que instancie mal un `DataTransformer` o
llame a un extractor sin `--output` sobrescribe datos reales. El fixture
`sin_produccion` corta esa posibilidad de raíz y se aplica a toda la suite.

Regla número dos: **ningún test toca la red.** Los pipelines se ejercitan con
la API simulada mediante `api_falsa`.
"""

import csv
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

FIXTURES = PROJECT_ROOT / "data"

# Pares de extracciones reales que sirven de caso de oro para el CDC.
PAR_IDENTICO = (
    FIXTURES / "raw" / "test_extraccion_1273_2026-04-01_120000_a_2026-05-01_120000_20260824_171142.csv",
    FIXTURES / "raw" / "test_extraccion_1273_2026-04-01_120000_a_2026-05-01_120000_20260826_094831.csv",
)
VENTANA_A = "sabana_2026-02-08_00_00_2026-03-09_23_59_59_"
VENTANA_B = "sabana_2026-02-22_00_00_2026-03-23_23_59_59_"
SOLAPE = ("2026-02-22 00:00:00", "2026-03-09 23:59:59")


@pytest.fixture(autouse=True)
def sin_produccion(monkeypatch, tmp_path):
    """Redirige todas las rutas de datos a un temporal, para toda la suite.

    Es `autouse`: no hay forma de olvidarse de aplicarlo. Si un test escribe
    sin pasar rutas explícitas, escribe en el temporal y no en Drive.
    """
    from config import settings

    # Se toman las claves de la configuración real: si mañana se agrega una
    # ruta nueva, queda aislada sin tener que acordarse de tocar este fixture.
    destinos = {clave: str(tmp_path / clave) for clave in settings.PATHS}
    monkeypatch.setattr(settings, "PATHS", destinos)

    # Los módulos hacen `from config.settings import PATHS`, así que guardan su
    # propia referencia. Se importan aquí a propósito para poder parchearlos
    # todos, aunque el test todavía no los haya importado.
    import importlib
    for nombre in (
        "typack_etl.extractor.pipelines.extractor_30m",
        "typack_etl.transformer.transformer",
        # Los scripts de operación también guardan su propia referencia, y son
        # los que resuelven las rutas por defecto de una corrida real.
        "scripts.run_daily_etl",
        "scripts.stats_tracker",
        "scripts.recarga_historica",
    ):
        monkeypatch.setattr(importlib.import_module(nombre), "PATHS", destinos, raising=False)
    return destinos


@pytest.fixture(autouse=True)
def sin_red(monkeypatch):
    """Bloquea cualquier salida a la red durante la suite.

    Un test que se olvide de simular la API golpearía la API real de MT
    Solutions con las credenciales de producción. Es preferible que falle
    ruidosamente.
    """
    import requests

    def prohibido(self, method, url, *a, **kw):
        raise RuntimeError(
            f"La suite no puede salir a la red ({method} {url}). "
            "Use el fixture api_falsa para simular la respuesta."
        )

    monkeypatch.setattr(requests.Session, "request", prohibido)


@pytest.fixture
def api_falsa(monkeypatch):
    """Reemplaza la API por una función que devuelve filas fijas.

    Uso:
        api_falsa(filas)                      # siempre las mismas
        api_falsa(lambda t, s, e, d: ...)     # respuesta calculada
    """
    from typack_etl.extractor.pipelines import extractor_30m

    def instalar(respuesta):
        fn = respuesta if callable(respuesta) else (lambda t, s, e, d=None: list(respuesta))
        monkeypatch.setattr(extractor_30m, "get_token", lambda: "token-de-prueba")
        monkeypatch.setattr(extractor_30m, "get_sabana_report", fn)
        return fn

    return instalar


@pytest.fixture
def reloj(monkeypatch):
    """Congela el reloj UTC del extractor en un instante dado."""
    from typack_etl.extractor.pipelines import extractor_30m

    def instalar(momento):
        monkeypatch.setattr(extractor_30m, "now_utc", lambda: momento)
        return momento

    return instalar


def _duration_coherente(start, end):
    """`duration` que le corresponde al intervalo, en minutos reales.

    Antes era "60.0" fijo, lo que producía filas imposibles: un turno de diez
    horas que declaraba durar una. El detector de `audit/reloj.py` compara
    justamente esas dos cosas, así que un fixture incoherente lo hace disparar
    en tests que no tienen nada que ver con el reloj. Los tests que quieren un
    desajuste a propósito pasan `duration=` explícito.
    """
    from typack_etl.audit.reloj import minutos_reales
    from typack_etl.extractor.utils.date_utils import parse_api_datetime

    ini, fin = parse_api_datetime(start), parse_api_datetime(end)
    if ini is None or fin is None or fin < ini:
        return "60.0"
    return f"{minutos_reales(ini, fin):.4f}".rstrip("0").rstrip(".") or "0.0"


def segmento(start, end, **campos):
    """Fila con la forma que devuelve la API, con valores por defecto sensatos."""
    fila = {
        "start": start, "end": end, "duration": _duration_coherente(start, end),
        "device": "Termoformadora 8", "line": "TERMOFORMADO", "plant": "Planta Typack CL",
        "turn": "TD", "order": "2412", "sku": "BCNEWM7001", "product": "BAND CARNE",
        "format": "Un", "production": "10", "code_description": "", "code_u": "",
        "code_f": "", "code_t": "", "code_l": "", "code_m": "", "code_n": "",
        "comments": "", "user": "OPERADOR",
    }
    fila.update(campos)
    return fila


def leer_csv(path, delimitador=";"):
    """Lee un CSV de datos o de bitácora como lista de dicts."""
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        return [dict(r) for r in csv.DictReader(f, delimiter=delimitador)]


def hay_fixture(*paths):
    """Salta el test si los datos reales no están en el checkout."""
    faltan = [p for p in paths if not Path(p).exists()]
    if faltan:
        pytest.skip(f"faltan fixtures reales: {[Path(p).name for p in faltan]}")
