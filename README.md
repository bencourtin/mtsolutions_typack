# MTSolutions Typack ETL

Pipeline ETL para extracción y transformación de datos de producción y detenciones desde la API de [MTSolutions](https://mts2.mtsolutions.io), orientado a líneas de Termoformado y Extrusión de Typack Chile.

## Estructura del proyecto

```
mtsolutions_typack/
├── .env                  # Secretos y rutas (gitignored)
├── .env.example          # Template de configuración
├── pyproject.toml        # Metadata y dependencias del proyecto
│
├── config/
│   ├── settings.py       # Punto único de carga de configuración
│   ├── lineas.json       # Definición de líneas/máquinas a extraer
│   └── horarios.json     # Turnos y horarios de Termoformado
│
├── src/
│   └── typack_etl/
│       ├── extractor/
│       │   ├── pipelines/
│       │   │   └── extractor_30m.py       # Extracción ventana móvil 30 días
│       │   └── services/
│       │       ├── auth.py                # Autenticación JWT contra API
│       │       └── reports.py             # Consumo de reporte "sabana"
│       └── transformer/
│           └── transformer.py             # Transformación y consolidación
│
├── scripts/
│   ├── run_daily_etl.py       # Orquestador diario (extracción + transformación)
│   ├── run_daily_etl.bat      # Batch para Windows Task Scheduler
│   ├── run_weekly_report.bat  # Batch para reporte semanal
│   ├── notify.py              # Notificaciones por email (errores y reportes)
│   └── stats_tracker.py       # Tracking de estadísticas de ejecución
│
├── data/
│   ├── raw/          # CSVs crudos extraídos de la API
│   ├── processed/    # CSVs transformados por línea/mes
│   └── external/     # Datos externos
│
├── docs/             # Documentación detallada
├── notebooks/        # Notebooks de exploración
├── logs/             # Logs de ejecución
└── tests/            # Tests unitarios
```

## Requisitos

- Python >= 3.10
- Acceso a la API MTSolutions (credenciales)
- Acceso a Google Drive compartido (para rutas de producción)

## Instalación

```bash
# 1. Clonar el repositorio
git clone https://github.com/bencourtin/mtsolutions_typack.git
cd mtsolutions_typack

# 2. Crear y activar entorno virtual
python -m venv .venv
.venv\Scripts\Activate.ps1    # Windows PowerShell
# source .venv/bin/activate   # Linux/Mac

# 3. Instalar como paquete editable (resuelve todos los imports automáticamente)
pip install -e .

# 4. Configurar variables de entorno
copy .env.example .env
# Editar .env con credenciales y rutas reales
```

## Configuración

Toda la configuración se centraliza en un archivo `.env` en la raíz del proyecto y se accede vía `config/settings.py`:

| Variable | Descripción |
|---|---|
| `API_USER` / `API_PASS` | Credenciales de la API MTSolutions |
| `SMTP_SERVER`, `SMTP_PORT`, `SMTP_PASSWORD` | Configuración SMTP para notificaciones |
| `EMAIL_RECIPIENTS_ERROR` | Destinatarios de alertas de error (separados por coma) |
| `EMAIL_RECIPIENTS_WEEKLY` | Destinatarios del reporte semanal |
| `PATH_RAW` | Ruta donde se guardan los CSVs crudos |
| `PATH_PROCESSED` | Ruta donde se guardan los CSVs transformados |
| `PATH_CONSOLIDATED` | Ruta del archivo consolidado final |
| `PATH_LOGS` | Ruta de logs de ejecución |

Los datos de negocio (líneas y horarios) se mantienen en JSON dentro de `config/`:

- **`lineas.json`** — Lista de líneas con `name` y `devid` (TF_2..TF_10, EXT_14..EXT_16)
- **`horarios.json`** — Turnos de Termoformado (día 08:00-18:00, noche 22:00-08:00)

### Uso en código

```python
from config.settings import PATHS, API_USER, API_PASS, EMAIL_CONFIG, load_lineas, load_horarios
```

## Uso

### Ejecución diaria (extracción + transformación)

```bash
python scripts/run_daily_etl.py
```

Este comando:
1. Ejecuta el **extractor de ventana móvil** (`extractor_30m`), que re-consulta
   los últimos 35 días operativos de todas las líneas — así una detención
   reclasificada después del cierre de mes se vuelve a extraer
2. Ejecuta el **transformer**, que procesa, normaliza y consolida
3. Publica las tablas de auditoría junto al consolidado, si la transformación
   terminó bien
4. Envía email de alerta si algún paso falla

Toma un lock para que dos corridas no se pisen: si ya hay una en curso, sale con
código 2 sin hacer nada.

### Extractor ventana móvil (el del ETL diario)

```bash
# Ventana por defecto (35 días), todas las líneas
python -m src.typack_etl.extractor.pipelines.extractor_30m

# Últimos 7 días
python -m src.typack_etl.extractor.pipelines.extractor_30m --days 7

# Una línea, rango a medida (en UTC)
python -m src.typack_etl.extractor.pipelines.extractor_30m --devid 1273 \
    --start-date "2026-03-01 12:00:00" --end-date "2026-04-01 12:00:00"
```

### Recarga histórica

```bash
# Ver el plan sin tocar nada
python scripts/recarga_historica.py --dry-run

# Reconstruir el rango completo con el esquema auditable
python scripts/recarga_historica.py
```

Ver [docs/PRODUCTION_SETUP.md](docs/PRODUCTION_SETUP.md) §7 antes de correrla:
el destino tiene que estar vacío o la trazabilidad sale mal.

### Transformer

```bash
python -m src.typack_etl.transformer.transformer
```

Procesa todos los años disponibles en `PATH_RAW`, aplica transformaciones y genera:
- CSVs procesados por línea/mes en `PATH_PROCESSED`
- Archivo consolidado único en `PATH_CONSOLIDATED`

### Reporte semanal

```bash
python scripts/run_daily_etl.py --weekly-report
```

### Notificaciones

```bash
# Email de prueba
python scripts/notify.py --test
```

## Transformaciones aplicadas

El transformer aplica las siguientes operaciones sobre los datos crudos:

1. **Renombrado de columnas** — De inglés API a español (ej: `product` → `Producto`)
2. **Normalización de códigos de detención** — Unificación de variantes ("Periodo no laboral", "PERIODO NO LABORAL" → "Período no laboral")
3. **Clasificación de turnos no usados** — Detenciones "No asignado" en Termoformado se reclasifican como "Fines de Semana" o "Turno No Usado" según horario
4. **Cálculo de minutos extras** — Tiempo trabajado después del horario de término del turno
5. **Clasificación de registros** — Producción, Planificado, No planificado, No producido

## Automatización (Windows Task Scheduler)

Las definiciones de tarea importables están en [`deploy/`](deploy/README.md):
la diaria a las 06:00 (`scripts/run_daily_etl.bat`) y la semanal los lunes a las
09:00 (`scripts/run_weekly_report.bat`). Se importan con `schtasks /create /xml`
tras reemplazar dos marcadores.

El procedimiento completo de instalación del servidor —incluido por qué Google
Drive tiene que estar en modo Reflejar— está en
[docs/PRODUCTION_SETUP.md](docs/PRODUCTION_SETUP.md).

## Documentación adicional

- [docs/ARQUITECTURA_ETL.md](docs/ARQUITECTURA_ETL.md) — Cómo funciona el proceso y por qué
- [docs/EXTRACTOR_USAGE.md](docs/EXTRACTOR_USAGE.md) — Uso detallado del extractor
- [docs/TRANSFORMER_CONFIG.md](docs/TRANSFORMER_CONFIG.md) — Configuración del transformer
- [docs/PRODUCTION_SETUP.md](docs/PRODUCTION_SETUP.md) — Despliegue en producción
- [deploy/README.md](deploy/README.md) — Tareas programadas
