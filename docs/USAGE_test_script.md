# Guía de Uso del Script de Descarga `Extractor_mensual.py`

Este documento describe todas las combinaciones relevantes de argumentos para el script:

```
python -m src.typack_etl.extractor.pipelines.test [opciones]
```

El script descarga el reporte "sabana" y genera:
- CSVs (delimitador `;`, decimales con `,`)
- Logs `plan_<timestamp>.csv` y `results_<timestamp>.csv` en el directorio de logs

## Resumen de Argumentos

| Argumento | Obligatorio | Default | Aplica | Descripción |
|-----------|------------|---------|--------|-------------|
| `--start-date` | No | Primer día mes actual 00:00 | Single / Multi | Fecha/hora inicio: formato libre aceptado por la API. |
| `--end-date` | No | Último día mes actual 23:59:59 | Single / Multi | Fecha/hora fin. |
| `--devid` | No | (omitido) | Single | Si se indica, descarga solo esa línea. Si se omite, modo multi-línea. |
| `--lineas-json` | No | `config/lineas.json` | Multi | Fuente de líneas (lista de objetos con `name` y `devid`). |
| `--output` | No | `data/processed` + nombre generado | Single / Multi | Archivo CSV destino (single) o directorio base (multi). Si en multi se da un `.csv`, usa su carpeta padre. |
| `--credenciales` | No | `config/credenciales.json` | Ambos | Archivo con `USER` y `PASS`. |
| `--logs-dir` | No | `data/processed/logs` | Ambos | Carpeta donde se guardan `plan_*.csv` y `results_*.csv`. |
| `--no-progress` | No | (false) | Multi | Desactiva la barra de progreso. |

## Comportamiento Automático

- Si falta `--start-date` o `--end-date`, se calcula el rango del mes en curso: 
  - Inicio: `YYYY-MM-01 00:00`
  - Fin: `YYYY-MM-<Último Día> 23:59:59`
- Modo Single-Line: Se activa si se usa `--devid`.
- Modo Multi-Línea: Se activa si NO se usa `--devid`; procesa todos los `devid` de `lineas.json`.
- Logs: 
  - `plan_<timestamp>.csv`: listado previo con columna `output_path` esperada.
  - `results_<timestamp>.csv`: resultado por línea (filas descargadas, éxito, error).
- Progress bar: Sólo aparece en modo multi-línea (se oculta con `--no-progress`).

## Nombres de Archivos CSV Generados

Formato Single-Line:
```
sabana_{start}_{end}_{devid}.csv
```

Formato Multi-Línea (por cada línea):
```
sabana_{start}_{end}_{name}_{devid}.csv
```
Los componentes se normalizan para eliminar espacios/caracteres no permitidos.

## Ejemplos: Modo Single-Line

### 1. Single-Line con rango mensual automático
```powershell
python -m src.typack_etl.extractor.pipelines.test --devid 1268
```

### 2. Single-Line con fechas explícitas
```powershell
python -m src.typack_etl.extractor.pipelines.test --start-date "2025-11-01 00:00" --end-date "2025-11-30 23:59:59" --devid 1268
```

### 3. Single-Line con archivo de salida específico
```powershell
python -m src.typack_etl.extractor.pipelines.test --devid 1268 --output "data\raw\sabana_TF2_noviembre.csv"
```

### 4. Single-Line cambiando credenciales y logs
```powershell
python -m src.typack_etl.extractor.pipelines.test --devid 1268 --credenciales config\credenciales_alt.json --logs-dir data\processed\logs_mensual
```

### 5. Single-Line sólo ajustando fechas de inicio (fin automático)
```powershell
python -m src.typack_etl.extractor.pipelines.test --start-date "2025-11-05 00:00" --devid 1268
```
(Usará fin = último día del mes a las 23:59:59)

### 6. Single-Line sólo ajustando fecha de fin (inicio automático)
```powershell
python -m src.typack_etl.extractor.pipelines.test --end-date "2025-11-20 23:59:59" --devid 1268
```
(Usará inicio = primer día del mes a las 00:00)

## Ejemplos: Modo Multi-Línea

### 1. Multi-Línea con rango mensual automático
```powershell
python -m src.typack_etl.extractor.pipelines.test
```

### 2. Multi-Línea con fechas explícitas
```powershell
python -m src.typack_etl.extractor.pipelines.test --start-date "2025-11-01 00:00" --end-date "2025-11-30 23:59:59"
```

### 3. Multi-Línea a un directorio específico
```powershell
python -m src.typack_etl.extractor.pipelines.test --output "data\raw"
```
(Crea múltiples archivos dentro de `data\raw`)

### 4. Multi-Línea con `--output` apuntando a archivo `.csv`
```powershell
python -m src.typack_etl.extractor.pipelines.test --output "data\raw\sabana_todas.csv"
```
(Se ignora el nombre del archivo y se usa su carpeta padre `data\raw` como destino)

### 5. Multi-Línea usando otro `lineas.json`
```powershell
python -m src.typack_etl.extractor.pipelines.test --lineas-json config\lineas_custom.json
```

### 6. Multi-Línea sin barra de progreso
```powershell
python -m src.typack_etl.extractor.pipelines.test --no-progress
```

### 7. Multi-Línea con directorio de logs personalizado
```powershell
python -m src.typack_etl.extractor.pipelines.test --logs-dir data\processed\logs_mensual
```

### 8. Multi-Línea combinando fechas, logs, salida y sin progreso
```powershell
python -m src.typack_etl.extractor.pipelines.test --start-date "2025-11-01 00:00" --end-date "2025-11-15 23:59:59" --output "data\mensual" --logs-dir data\processed\logs_mensual --no-progress
```

### 9. Multi-Línea con credenciales alternativas
```powershell
python -m src.typack_etl.extractor.pipelines.test --credenciales config\credenciales_alt.json
```

## Logs: Ejemplos de Contenido

`plan_20251119_170231.csv`
```
line;devid;start;end;output_path
TF_2;1268;2025-11-01 00:00;2025-11-30 23:59:59;data/processed/sabana_2025-11-01 00_00_2025-11-30 23_59_59_TF_2_1268.csv
...
```

`results_20251119_170234.csv`
```
line;devid;start;end;rows;success;error
TF_2;1268;2025-11-01 00:00;2025-11-30 23:59:59;1537;1;
TF_3;1269;2025-11-01 00:00;2025-11-30 23:59:59;0;0;SIN_DATOS
...
```

## Manejo de Errores

- Si un `devid` falla: se registra en `results_*.csv` con `success=0` y `error` con el mensaje.
- Si la respuesta viene vacía: `rows=0`, `success=0`, `error=SIN_DATOS`.
- Si `lineas.json` no existe en modo multi-línea: el script finaliza con código distinto de 0.

## Estrategias y Buenas Prácticas

- Para evitar saturar la API: ejecutar lotes en horarios de baja carga.
- Verificar que `config/credenciales.json` contenga credenciales vigentes antes de lanzarlo.
- Versionar los logs mensuales (`--logs-dir data/processed/logs_mensual`) para auditoría.
- Usar un directorio separado para pruebas (`data/raw_pruebas`) y luego mover a `processed`.

## Combinaciones Principales (Resumen)

| Escenario | Comando | Notas |
|-----------|---------|-------|
| Single auto fechas | `python -m ... --devid 1268` | Usa mes actual completo |
| Single fechas explícitas | `--start-date ... --end-date ... --devid 1268` | Rango custom |
| Single salida custom | `--devid 1268 --output data\raw\out.csv` | Archivo destino directo |
| Multi auto fechas | `python -m ...` | Todas las líneas del mes |
| Multi dir custom | `--output data\raw` | Directorio base |
| Multi sin progreso | `--no-progress` | Oculta barra |
| Multi logs custom | `--logs-dir data\processed\logs_mensual` | Centraliza logs |
| Multi + otras lineas | `--lineas-json config\lineas_custom.json` | Set alternativo |
| Multi combinado | `--start-date ... --end-date ... --output ... --logs-dir ... --no-progress` | Máximo control |

## Preguntas Frecuentes

**¿Puedo usar sólo `--start-date` y dejar que calcule el fin?** Sí. Si omites `--end-date`, tomará el último día del mes 23:59:59.

**¿Qué pasa si pongo `--output archivo.csv` en modo multi-línea?** Ignora el nombre de archivo y usa su carpeta padre para todos los CSV generados.

**¿Necesito formatear las fechas con segundos?** No estrictamente; usa el formato que la API acepte. El script no valida el patrón, sólo lo pasa.

**¿Por qué los decimales llevan coma?** Para compatibilidad con entornos donde el separador decimal estándar es la coma (p.ej. locales hispanohablantes / Excel configurado en español).

## Siguientes Mejoras Posibles

- Reintentos por `devid` con backoff exponencial.
- Exportar un JSON resumen consolidado.
- Añadir parámetro `--parallel` para descargas concurrentes.
- Integrar un límite de tasa (rate limiting) configurable.

---
Última actualización: 2025-11-19
