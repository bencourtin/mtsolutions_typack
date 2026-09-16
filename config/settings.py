"""
Módulo centralizado de configuración para el ETL Typack.

Carga secretos y rutas desde .env, y datos de negocio desde JSON.
Uso:
    from config.settings import PATHS, API_USER, API_PASS, EMAIL_CONFIG, load_lineas, load_horarios
"""

import os
import json
from datetime import date, datetime
from pathlib import Path
from dotenv import load_dotenv

# Cargar .env desde la raíz del proyecto
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

_CONFIG_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Credenciales API
# ---------------------------------------------------------------------------
API_USER = os.getenv("API_USER", "")
API_PASS = os.getenv("API_PASS", "")

# ---------------------------------------------------------------------------
# Operación del extractor
# ---------------------------------------------------------------------------
# El corte del día operativo y el desfase del reloj del proveedor NO se
# configuran: los dos dependen de la fecha del dato y se calculan en
# `typack_etl.extractor.utils.date_utils`. El corte es el cambio de turno
# TD/TN, las 08:00 de hora local de planta (config/horarios.json), y el reloj
# del proveedor es hora chilena con las reglas de DST anteriores a 2019.
#
# Fueron `CORTE_OPERATIVO_UTC` y `OFFSET_PLANTA_HORAS` hasta el 2026-09-16.
# Entre los dos había que editarlos cuatro veces al año y un reproceso los
# aplicaba con el valor de hoy a datos de cualquier fecha: ver
# docs/CONTEXTO_CAMBIO_DE_HORA.md.

# Ventana móvil del ETL diario, en días operativos hacia atrás desde el último
# corte cerrado. 30 días es la ventana en que se observan reasignaciones de
# detenciones; los días extra son holgura para no perder el borde por corridas
# fallidas.
EXTRACTOR_WINDOW_DAYS = int(os.getenv("EXTRACTOR_WINDOW_DAYS", "35"))

# Días que se conservan las bitácoras plan_/results_ de cada corrida. El libro
# de auditoría no se rota: ese es el histórico que hay que conservar.
RETENCION_LOGS_DIAS = int(os.getenv("RETENCION_LOGS_DIAS", "90"))

# Días que se conservan las respuestas crudas de la API. Es la ventana en que
# se puede peritar qué decía exactamente el dato en una fecha dada.
RETENCION_SNAPSHOTS_DIAS = int(os.getenv("RETENCION_SNAPSHOTS_DIAS", "90"))

# Cuántas líneas pueden fallar antes de que el ETL diario aborte la
# transformación en vez de publicar un consolidado incompleto.
ETL_MAX_LINEAS_FALLIDAS = int(os.getenv("ETL_MAX_LINEAS_FALLIDAS", "2"))

# ---------------------------------------------------------------------------
# Rutas de datos
# ---------------------------------------------------------------------------
PATHS = {
    "raw": os.getenv("PATH_RAW", "data/raw"),
    "processed": os.getenv("PATH_PROCESSED", "data/processed"),
    "consolidated": os.getenv("PATH_CONSOLIDATED", "data/consolidated"),
    "stream_data": os.getenv("PATH_STREAM_DATA", "data/stream_data"),
    "logs": os.getenv("PATH_LOGS", "logs"),
    # Libro de auditoría de cambios de asignación y cobertura diaria.
    "audit": os.getenv("PATH_AUDIT", "data/audit"),
    # Respuestas crudas de la API, tal como llegaron. Nunca se sobrescriben.
    "snapshots": os.getenv("PATH_SNAPSHOTS", "data/snapshots"),
}

# ---------------------------------------------------------------------------
# Configuración SMTP / Email
# ---------------------------------------------------------------------------
SMTP_CONFIG = {
    "server": os.getenv("SMTP_SERVER", "smtp.gmail.com"),
    "port": int(os.getenv("SMTP_PORT", "587")),
    "use_tls": os.getenv("SMTP_USE_TLS", "true").lower() == "true",
    "username": os.getenv("SMTP_USERNAME", ""),
    "password": os.getenv("SMTP_PASSWORD", ""),
}

EMAIL_CONFIG = {
    "smtp": SMTP_CONFIG,
    "sender": {
        "email": SMTP_CONFIG["username"],
        "name": os.getenv("EMAIL_SENDER_NAME", "MT Typack Sabana ETL"),
    },
    "recipients": {
        "error": [
            e.strip()
            for e in os.getenv("EMAIL_RECIPIENTS_ERROR", "").split(",")
            if e.strip()
        ],
        "weekly_report": [
            e.strip()
            for e in os.getenv("EMAIL_RECIPIENTS_WEEKLY", "").split(",")
            if e.strip()
        ],
    },
    "enabled": os.getenv("EMAIL_ENABLED", "true").lower() == "true",
    "weekly_report_day": int(os.getenv("EMAIL_WEEKLY_REPORT_DAY", "1")),
}

# ---------------------------------------------------------------------------
# Datos de negocio (JSON)
# ---------------------------------------------------------------------------

def load_lineas() -> list:
    """Carga la lista de líneas desde config/lineas.json."""
    lineas_path = _CONFIG_DIR / "lineas.json"
    with lineas_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    items = data.get("lineas", [])
    out = []
    for it in items:
        if isinstance(it, dict) and "devid" in it:
            out.append({"name": str(it.get("name", it["devid"])), "devid": it["devid"]})
    return out


def load_horarios() -> dict:
    """Carga `config/horarios.json` tal cual, validando que tenga la forma esperada.

    Esquema v2: `turnos.<TURNO>.nombre`, `areas.<Area>.turnos_permitidos` y
    `vigencias[]`, cada una con `id`, `vigencia_desde` (ISO), `vigente` y
    `horarios.<TURNO>.<dia>.{inicio,fin}`.

    Las horas se dejan como texto: quien las interpreta es
    `typack_etl.transformer.turnos`, que además aloja las reglas de negocio que
    el JSON no puede expresar.

    Se valida al cargar y no al usar porque un horario mal escrito se traduce en
    registros clasificados en el turno equivocado, que es un error silencioso:
    nadie lo ve hasta que el BI muestra producción en un turno que no existió.
    """
    horarios_path = _CONFIG_DIR / "horarios.json"
    with horarios_path.open("r", encoding="utf-8") as f:
        cfg = json.load(f)

    faltan = [k for k in ("turnos", "areas", "vigencias") if k not in cfg]
    if faltan:
        raise ValueError(f"horarios.json sin las claves {faltan}. Ver docs/TRANSFORMER_CONFIG.md")

    vigentes = [v for v in cfg["vigencias"] if v.get("vigente")]
    if not vigentes:
        raise ValueError("horarios.json: no hay ninguna vigencia con vigente=true")

    desdes = set()
    for v in vigentes:
        ident = v.get("id") or "(sin id)"
        if "vigencia_desde" not in v:
            raise ValueError(f"horarios.json: la vigencia {ident} no declara vigencia_desde")
        try:
            desde = date.fromisoformat(v["vigencia_desde"])
        except ValueError as e:
            raise ValueError(f"horarios.json: vigencia_desde invalido en {ident}: {e}") from e
        if desde in desdes:
            raise ValueError(
                f"horarios.json: hay dos vigencias vigentes desde {desde}; "
                "no se puede decidir cual rige"
            )
        desdes.add(desde)

        if not v.get("horarios"):
            raise ValueError(f"horarios.json: la vigencia {ident} no trae horarios")
        for turno, por_dia in v["horarios"].items():
            for dia, franja in por_dia.items():
                for borde in ("inicio", "fin"):
                    if borde not in franja:
                        raise ValueError(
                            f"horarios.json: falta '{borde}' en {ident}.horarios.{turno}.{dia}"
                        )
                    texto = franja[borde]
                    datetime.strptime(texto, "%H:%M" if len(texto) == 5 else "%H:%M:%S")

    # Un area no puede habilitar un turno que ninguna vigencia define.
    for area, datos in cfg["areas"].items():
        for turno in datos.get("turnos_permitidos") or []:
            if not any(turno in v["horarios"] for v in vigentes):
                raise ValueError(
                    f"horarios.json: el area {area} habilita {turno}, "
                    "que ninguna vigencia vigente define"
                )

    return cfg
