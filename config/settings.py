"""
Módulo centralizado de configuración para el ETL Typack.

Carga secretos y rutas desde .env, y datos de negocio desde JSON.
Uso:
    from config.settings import PATHS, API_USER, API_PASS, EMAIL_CONFIG, load_lineas, load_horarios
"""

import os
import json
from datetime import datetime
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
# Rutas de datos
# ---------------------------------------------------------------------------
PATHS = {
    "raw": os.getenv("PATH_RAW", "data/raw"),
    "processed": os.getenv("PATH_PROCESSED", "data/processed"),
    "consolidated": os.getenv("PATH_CONSOLIDATED", "data/consolidated"),
    "stream_data": os.getenv("PATH_STREAM_DATA", "data/stream_data"),
    "logs": os.getenv("PATH_LOGS", "logs"),
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
    """Carga horarios desde config/horarios.json y convierte strings a objetos time."""
    horarios_path = _CONFIG_DIR / "horarios.json"
    with horarios_path.open("r", encoding="utf-8") as f:
        horarios = json.load(f)
    for _key, horario in horarios.items():
        horario["hora_inicio"] = datetime.strptime(horario["hora_inicio"], "%H:%M").time()
        horario["hora_fin"] = datetime.strptime(horario["hora_fin"], "%H:%M").time()
    return horarios
