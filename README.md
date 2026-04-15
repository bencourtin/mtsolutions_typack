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
│       │   │   ├── extractor_mensual.py   # Extracción mensual
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
1. Ejecuta el **extractor mensual** para todas las líneas del mes en curso
2. Ejecuta el **transformer** que procesa, normaliza y consolida los datos
3. Envía email de alerta si algún paso falla

### Extractor mensual

```bash
# Todas las líneas, mes actual
python -m typack_etl.extractor.pipelines.extractor_mensual

# Línea específica
python -m typack_etl.extractor.pipelines.extractor_mensual --devid 1268

# Rango de fechas personalizado
python -m typack_etl.extractor.pipelines.extractor_mensual --start-date "2026-03-01 00:00" --end-date "2026-03-31 23:59:59"
```

### Extractor ventana móvil (30 días)

```bash
# Últimos 30 días, todas las líneas
python -m typack_etl.extractor.pipelines.extractor_30m

# Últimos 7 días
python -m typack_etl.extractor.pipelines.extractor_30m --days 7
```

### Transformer

```bash
python -m typack_etl.transformer.transformer
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
2. **División de registros en medianoche** — Registros que cruzan las 00:00 se dividen en dos, redistribuyendo producción y duración proporcionalmente
3. **Normalización de códigos de detención** — Unificación de variantes ("Periodo no laboral", "PERIODO NO LABORAL" → "Período no laboral")
4. **Clasificación de turnos no usados** — Detenciones "No asignado" en Termoformado se reclasifican como "Fines de Semana" o "Turno No Usado" según horario
5. **Cálculo de minutos extras** — Tiempo trabajado después del horario de término del turno
6. **Clasificación de registros** — Producción, Planificado, No planificado, No producido

## Automatización (Windows Task Scheduler)

### ETL diario
Ejecutar `scripts/run_daily_etl.bat` como tarea programada. El script valida Python, ejecuta el ETL y registra resultados en logs.

### Reporte semanal
Ejecutar `scripts/run_weekly_report.bat` los lunes a las 9:00. Genera y envía un reporte con métricas de la semana (tasa de éxito, filas extraídas/transformadas, líneas sin datos).

## Documentación adicional

- [docs/EXTRACTOR_USAGE.md](docs/EXTRACTOR_USAGE.md) — Uso detallado del extractor
- [docs/TRANSFORMER_CONFIG.md](docs/TRANSFORMER_CONFIG.md) — Configuración del transformer
- [docs/PRODUCTION_SETUP.md](docs/PRODUCTION_SETUP.md) — Despliegue en producción
