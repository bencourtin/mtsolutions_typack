# Guía de Uso - Scripts Extractor

Esta guía describe el uso de los scripts de extracción de datos del sistema Typack.

## Índice

- [Extractor de Últimos 30 Días (Ventana Móvil)](#extractor-de-últimos-30-días-ventana-móvil)
- [Extractor Mensual](#extractor-mensual)
- [Configuración Requerida](#configuración-requerida)
- [Estructura de Salida](#estructura-de-salida)

---

## Extractor de Últimos 30 Días (Ventana Móvil)

**Archivo:** `src/typack_etl/extractor/pipelines/extractor_30m.py`

### Descripción

Descarga datos del reporte "sabana" para una ventana móvil de días (por defecto 30 días), terminando en el día anterior a la ejecución.

### Reglas del Rango por Defecto

La ventana se alinea al **día operativo**, no al día calendario. Un día
operativo dura 24 h y va de corte a corte:

```
corte = 12:00:00 UTC = 08:00 de planta = cambio de turno TD/TN
```

- **Fecha fin:** el último corte ya cumplido, de modo que la ventana nunca
  incluye un turno a medio andar.
- **Fecha inicio:** fecha fin − `--days` días exactos.

Ejemplo: si son las 14:30 UTC del 3 de abril y `--days 35`, descarga desde
`2026-02-27 12:00:00` hasta `2026-04-03 12:00:00` — 35 turnos completos.

### Modos de Operación

#### Modo Single-Line (Una Línea)
Descarga datos de una sola línea especificada.

#### Modo Multi-Línea
Descarga datos de todas las líneas definidas en el archivo `lineas.json`.

### Parámetros

| Parámetro | Requerido | Default | Descripción |
|-----------|-----------|---------|-------------|
| `--days` | No | 30 | Cantidad de días hacia atrás (incluyendo ayer) |
| `--start-date` | No | Automático | Override de fecha/hora de inicio (formato: `YYYY-MM-DD HH:MM`) |
| `--end-date` | No | Automático | Override de fecha/hora de fin (formato: `YYYY-MM-DD HH:MM:SS`) |
| `--devid` | No | - | ID de línea específica (activa modo single-line) |
| `--output` | No | `<PATH_RAW>` | Directorio base de salida |
| `--logs-dir` | No | `<PATH_LOGS>` | Directorio para archivos de log |
| `--no-progress` | No | False | Oculta barra de progreso en modo multi-línea |

> `--days` toma su valor por defecto de `EXTRACTOR_WINDOW_DAYS` en `.env` (35).
> Las líneas se leen siempre de `config/lineas.json` y las credenciales de `.env`,
> vía `config/settings.py`. Los antiguos `--lineas-json` y `--credenciales` ya no existen.

### Escritura por partición

Este es el pipeline del ETL diario. Los archivos se particionan por el **mes
operativo de cada registro**, no por la fecha de la corrida, de modo que una
ventana que cruza fin de mes escribe en dos carpetas.

El mes operativo va del día 1 en el corte al día 1 del mes siguiente en el
corte. Un registro anterior al corte del día 1 pertenece al mes anterior,
porque forma parte del turno que arrancó el último día de ese mes:

| Registro | Mes operativo |
|----------|---------------|
| `2026-03-31 22:00` | marzo |
| `2026-04-01 00:00` | marzo |
| `2026-04-01 06:00` | **marzo** |
| `2026-04-01 08:00` | abril |

Dentro de cada partición sólo se reemplaza el tramo que la ventana cubre:

```
produccion_2026_marzo_TF_8.csv     ventana: 17-mar -> 19-abr

  1-mar .. 16-mar   se conservan intactos (fuera de la ventana)
 17-mar .. 31-mar   se reemplazan por lo que devuelve la API ahora
```

Consecuencias:

- La corrida diaria refresca los últimos N días **sin destruir el histórico del mes**.
- Si la API deja de reportar un segmento que está dentro de la ventana, ese
  segmento desaparece del archivo: el archivo refleja lo que la API dice hoy.
- Las particiones se recorren según la ventana, no según los datos recibidos, de
  modo que un mes sin producción también se refresca.
- **Una línea que falla no escribe nada**: sus archivos quedan exactamente como estaban.
- Si la API vuelve a reportar un segmento que ya estaba, gana la versión nueva: el
  archivo nunca acumula dos copias del mismo `(start, end)`.

### Auditoría de cambios

Antes de escribir cada partición, el extractor compara lo que la API devuelve
contra lo que el archivo ya tenía. Ese diff es el libro de auditoría, y es lo
que permite responder qué detenciones cambiaron de asignación.

**Archivos** (en `PATH_AUDIT`, default `data/audit`):

| Archivo | Contenido |
|---------|-----------|
| `detenciones_audit.csv` | Libro append-only: una fila por campo modificado |
| `cobertura_diaria.csv` | Minutos reportados por línea y día, con su `run_id` |
| `ultimas_corridas.json` | `run_id` de la última corrida que escribió cada línea |

**Tipos de cambio:**

| tipo_cambio | Cuándo |
|-------------|--------|
| `REASIGNACION` | Cambió algún código de detención. Es el evento que se busca |
| `OTRO_CAMBIO` | Cambió un comentario, usuario o producción, no el código |
| `RECORTE_SEGMENTO` | El segmento se volvió a cortar (merge o split); se registra origen → destino |
| `DESAPARECIDO` | La API dejó de reportar el segmento y nada lo reemplaza |
| `REAPARECIDO` | Una clave que el libro daba por perdida volvió a aparecer |
| `NUEVO` | Alta normal. **No se escribe al libro** salvo que se pida: en una ventana de 35 días son cientos por línea y día, y duplicarían los archivos de datos |

**Identidad de un segmento:** `sha1(devid + start + end)`. Verificado sobre
26.776 filas reales: `start` por sí solo colisiona en 0,17 % de los casos.

**Alerta de cobertura:** si un día ya cerrado pasa a reportar menos minutos que
en la corrida anterior, se avisa al final de la corrida. Es el patrón de la
pérdida de tiempo que se observó entre las extracciones de marzo.

**Parámetros:**

| Parámetro | Descripción |
|-----------|-------------|
| `--audit-dir` | Directorio del libro (default: `PATH_AUDIT`) |
| `--no-audit` | Desactiva la detección de cambios (no recomendado) |

### Ejemplos de Uso

#### Ejemplo 1: Últimos 30 días, todas las líneas

```bash
python -m src.typack_etl.extractor.pipelines.extractor_30m
```

#### Ejemplo 2: Últimos 15 días, todas las líneas

```bash
python -m src.typack_etl.extractor.pipelines.extractor_30m --days 15
```

#### Ejemplo 3: Últimos 30 días, una línea específica

```bash
python -m src.typack_etl.extractor.pipelines.extractor_30m --devid 1270
```

#### Ejemplo 4: Rango personalizado, una línea

```bash
python -m src.typack_etl.extractor.pipelines.extractor_30m \
  --start-date "2025-01-01 00:00" \
  --end-date "2025-01-15 23:59:59" \
  --devid 1271
```

#### Ejemplo 5: Últimos 45 días sin barra de progreso

```bash
python -m src.typack_etl.extractor.pipelines.extractor_30m \
  --days 45 \
  --no-progress
```

### Archivos Generados

#### Archivos CSV de Datos

Una carpeta por mes tocado por la ventana, con el mismo nombre de archivo que
consume el transformer:

```
<PATH_RAW>/{año}/{mes}/produccion_{año}_{mes_nombre}_{nombre_linea}.csv
```

En modo single-line el nombre de la línea se resuelve desde `config/lineas.json`
a partir del `--devid`, de modo que el transformer también levante el archivo.

Formato:
- Delimitador: `;` (punto y coma)
- Decimales: `,` (coma)
- Encoding: UTF-8 con BOM
- Cabecera: esquema canónico fijo de 21 columnas (`functions/io.py:SABANA_COLUMNS`).
  La API omite las claves nulas; las columnas que falten se escriben vacías, de
  modo que todos los archivos de la serie tienen las mismas columnas en el mismo orden.

#### Archivos de Log

**Plan (antes de ejecutar):**
```
{logs-dir}/plan_YYYYMMDD_HHMMSS.csv
```

Columnas: `line;devid;start;end;output_path`

**Resultados (después de ejecutar):**
```
{logs-dir}/results_YYYYMMDD_HHMMSS.csv
```

Columnas: `line;devid;start;end;rows;success;error;detalle`

`detalle` resume lo escrito por partición, por ejemplo
`2026-03:14(+16 conservadas); 2026-04:19(+0 conservadas)`.

---

## Extractor Mensual

**Archivo:** `src/typack_etl/extractor/pipelines/extractor_mensual.py`

> **Sólo recargas históricas.** Este pipeline ya no forma parte del ETL diario.
> Su ventana es el mes calendario, por lo que no captura las reasignaciones
> posteriores al cierre de mes. El ETL diario usa `extractor_30m`.

### Descripción

Descarga datos del reporte "sabana" para un mes calendario (por defecto, el mes en curso).
Igual que el extractor de ventana móvil, reemplaza sólo el tramo declarado en
`--start-date`/`--end-date` y conserva el resto del archivo del mes.

### Reglas del Rango por Defecto

- **Fecha inicio:** Primer día del mes actual a las 00:00
- **Fecha fin:** Último día del mes actual a las 23:59:59

Ejemplo: Si estamos en enero 2026, descarga del 1 de enero al 31 de enero.

### Modos de Operación

Igual que el extractor de 30 días:
- **Modo Single-Line:** Una línea específica
- **Modo Multi-Línea:** Todas las líneas del archivo JSON

### Estructura de Carpetas

Los archivos se organizan por año y mes usando `config/config.json`:

```
data/processed/
  2026/
    01/
      produccion_2026_enero_Linea1.csv
      produccion_2026_enero_Linea2.csv
```

Logs también siguen esta estructura:
```
data/processed/logs/
  2026/
    01/
      plan_20260123_143522.csv
      results_20260123_143545.csv
```

### Parámetros

| Parámetro | Requerido | Default | Descripción |
|-----------|-----------|---------|-------------|
| `--start-date` | No | 1er día del mes | Override de fecha/hora de inicio |
| `--end-date` | No | Último día del mes | Override de fecha/hora de fin |
| `--devid` | No | - | ID de línea específica (activa modo single-line) |
| `--output` | No | `<PATH_RAW>/{año}/{mes}/` | Archivo o directorio de salida |
| `--logs-dir` | No | `<PATH_LOGS>/{año}/{mes}/` | Directorio para logs |
| `--no-progress` | No | False | Oculta barra de progreso en modo multi-línea |

> Las líneas se leen siempre de `config/lineas.json` y las credenciales de `.env`,
> vía `config/settings.py`. Los antiguos `--lineas-json` y `--credenciales` ya no existen.

### Ejemplos de Uso

#### Ejemplo 1: Mes actual, todas las líneas

```bash
python -m src.typack_etl.extractor.pipelines.extractor_mensual
```

#### Ejemplo 2: Mes actual, una línea específica

```bash
python -m src.typack_etl.extractor.pipelines.extractor_mensual --devid 1270
```

#### Ejemplo 3: Mes específico (diciembre 2025), todas las líneas

```bash
python -m src.typack_etl.extractor.pipelines.extractor_mensual \
  --start-date "2025-12-01 00:00" \
  --end-date "2025-12-31 23:59:59"
```

#### Ejemplo 4: Rango personalizado con directorio de salida

```bash
python -m src.typack_etl.extractor.pipelines.extractor_mensual \
  --start-date "2025-11-15 00:00" \
  --end-date "2025-12-15 23:59:59" \
  --output "data/custom_output/"
```

### Archivos Generados

#### Archivos CSV de Datos

**Modo Single-Line:**
```
data/processed/{año}/{mes}/produccion_{año}_{mes_nombre}_{devid}.csv
```

**Modo Multi-Línea:**
```
data/processed/{año}/{mes}/produccion_{año}_{mes_nombre}_{nombre_linea}.csv
```

Ejemplo: `data/processed/2026/01/produccion_2026_enero_Linea1.csv`

Formato:
- Delimitador: `;` (punto y coma)
- Decimales: `,` (coma)
- Encoding: UTF-8 con BOM

#### Archivos de Log

**Plan:**
```
data/processed/logs/{año}/{mes}/plan_YYYYMMDD_HHMMSS.csv
```

**Resultados:**
```
data/processed/logs/{año}/{mes}/results_YYYYMMDD_HHMMSS.csv
```

---

## Configuración Requerida

### Credenciales

**Ubicación:** `.env` en la raíz del proyecto (cargado por `config/settings.py`)

```
API_USER=tu_usuario
API_PASS=tu_contraseña
```

El antiguo `config/credenciales.json` quedó obsoleto y ya no se lee.

### Archivo de Líneas

**Ubicación:** `config/lineas.json`

```json
{
  "lineas": [
    {
      "name": "Línea Producción 1",
      "devid": 1270
    },
    {
      "name": "Línea Producción 2",
      "devid": 1271
    },
    {
      "name": "Línea Producción 3",
      "devid": 1272
    }
  ]
}
```

### Rutas de datos

**Ubicación:** `.env` en la raíz del proyecto. Ambos extractores las leen desde
`config.settings.PATHS`.

```
PATH_RAW=...
PATH_PROCESSED=...
PATH_CONSOLIDATED=...
PATH_LOGS=...
```

El antiguo `config/config.json` quedó obsoleto y ya no se lee.

---

## Estructura de Salida

### CSV de Datos

Los archivos CSV contienen las columnas retornadas por la API del reporte "sabana". Las columnas exactas dependen de la respuesta de la API, pero típicamente incluyen:

- Información de producción
- Métricas de rendimiento
- Timestamps de eventos
- Identificadores de línea/máquina

**Características del formato:**
- Delimitador de columnas: `;` (punto y coma)
- Separador decimal: `,` (coma)
- Encoding: UTF-8 con BOM (para compatibilidad con Excel)

### Plan Log (plan_*.csv)

Registra el plan de ejecución antes de iniciar las descargas:

```csv
line;devid;start;end;output_path
Linea1;1270;2026-01-01 00:00;2026-01-22 23:59:59;data/processed/...
Linea2;1271;2026-01-01 00:00;2026-01-22 23:59:59;data/processed/...
```

### Results Log (results_*.csv)

Registra los resultados de cada descarga:

```csv
line;devid;start;end;rows;success;error
Linea1;1270;2026-01-01 00:00;2026-01-22 23:59:59;1523;1;
Linea2;1271;2026-01-01 00:00;2026-01-22 23:59:59;0;0;SIN_DATOS
Linea3;1272;2026-01-01 00:00;2026-01-22 23:59:59;987;1;
```

Campos:
- `rows`: Cantidad de filas descargadas
- `success`: 1 si hubo datos, 0 si no
- `error`: Mensaje de error o "SIN_DATOS" si no hubo datos

---

## Casos de Uso Comunes

### 1. Extracción Diaria Automática (ventana móvil)

Es lo que ejecuta `scripts/run_daily_etl.py`. Manualmente:

```bash
python -m src.typack_etl.extractor.pipelines.extractor_30m --no-progress
```

### 2. Recarga Histórica de un Mes Cerrado

Sólo cuando haga falta rehacer un mes que ya salió de la ventana móvil:

```bash
python -m src.typack_etl.extractor.pipelines.extractor_mensual \
  --start-date "2025-11-01 00:00" --end-date "2025-11-30 23:59:59"
```

### 3. Extracción Histórica de un Mes Específico

```bash
python -m src.typack_etl.extractor.pipelines.extractor_mensual \
  --start-date "2025-11-01 00:00" \
  --end-date "2025-11-30 23:59:59"
```

### 4. Verificación de una Línea Específica

```bash
python -m src.typack_etl.extractor.pipelines.extractor_30m \
  --devid 1270 \
  --days 7
```

### 5. Extracción sin Barra de Progreso (para cron/scripts)

```bash
python -m src.typack_etl.extractor.pipelines.extractor_30m --no-progress
python -m src.typack_etl.extractor.pipelines.extractor_mensual --no-progress
```

---

## Solución de Problemas

### Error: "No se encontró lineas.json"

Verificar que existe el archivo `config/lineas.json` con la estructura correcta.

### Error de Autenticación

Verificar credenciales en `config/credenciales.json`.

### Sin Datos Descargados (0 filas)

- Verificar que el rango de fechas contiene datos
- Verificar que el `devid` es correcto
- Revisar el archivo `results_*.csv` para ver el error específico

### Archivos CSV sólo con Cabecera

Si un CSV trae únicamente la fila de cabecera, la API no retornó datos para ese
rango/línea. Revisar:
- Rango de fechas
- Estado de la línea en el sistema Typack
- Logs de resultados para más detalles

---

## Notas Adicionales

1. **Tres relojes:** la API **recibe** los parámetros en UTC y **devuelve** los
   timestamps en el reloj de su servidor, que es hora chilena con las reglas de
   horario de verano que el país usó hasta 2018. Por eso va una hora adelantado
   respecto de la planta entre el 1er domingo de abril y el 2º de mayo, y entre
   el 2º domingo de agosto y el 1º de septiembre. `--start-date` y `--end-date`
   se interpretan en UTC; el particionado y la auditoría trabajan en el reloj de
   la API; los turnos y las reglas horarias, en hora local (`date_utils.a_local`).
   Ver [`ARQUITECTURA_ETL.md`](ARQUITECTURA_ETL.md) §2.bis.

2. **Formato de Fechas:** Los argumentos `--start-date` y `--end-date` aceptan formato `YYYY-MM-DD HH:MM:SS`

3. **Interrupción:** Ambos scripts pueden interrumpirse con Ctrl+C de forma segura

4. **Performance:** En modo multi-línea, el proceso puede tardar varios minutos dependiendo de la cantidad de líneas

5. **Logs:** Los archivos de log (plan y results) se generan automáticamente y permiten auditoría completa de las ejecuciones
