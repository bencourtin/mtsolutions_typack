# División de Registros en Medianoche - Documentación

## Descripción

Este módulo implementa la funcionalidad de **dividir registros que cruzan medianoche** en dos registros separados, redistribuyendo la producción proporcionalmente al tiempo trabajado en cada tramo.

## Archivos

- **`transformer_midnight_split.py`**: Módulo de prueba con la funcionalidad de división
- **`transformer.py`**: Módulo principal (no modificado)

## Funcionalidad

### ¿Qué hace?

Cuando un registro tiene:
- `Fecha_Hora_Inicio` en un día (ej: 01-01-2025 22:30)
- `Fecha_Hora_Termino` en el día siguiente (ej: 02-01-2025 04:33)

El sistema lo divide en **dos registros**:

1. **Registro A**: Desde el inicio hasta las 23:59:59 del mismo día
2. **Registro B**: Desde las 00:00:00 del día siguiente hasta el fin original

### Recálculo de producción

La producción se redistribuye **proporcionalmente al tiempo trabajado**:

```
dur_total = fin_original - inicio_original
dur_A = 23:59:59 - inicio
dur_B = fin_original - 00:00:00

prod_A = producción_original × (dur_A / dur_total)
prod_B = producción_original - prod_A  # Para garantizar suma exacta
```

### Ejemplo

**Antes:**
| Fecha_Hora_Inicio | Fecha_Hora_Termino | Q_prod_total | Duracion(min) | Turno |
|-------------------|-------------------|--------------|---------------|-------|
| 01-01-2025 22:30  | 02-01-2025 04:33  | 4300         | 363           | TN    |

**Después:**
| Fecha_Hora_Inicio | Fecha_Hora_Termino | Q_prod_total | Duracion(min) | Turno |
|-------------------|-------------------|--------------|---------------|-------|
| 01-01-2025 22:30  | 01-01-2025 23:59  | 2131.41      | 89.98         | TN    |
| 02-01-2025 00:00  | 02-01-2025 04:33  | 2168.59      | 273.02        | TN    |

> **Nota**: La suma de producción y duración se mantiene exacta: 2131.41 + 2168.59 = 4300

## Cómo usar

### 1. Ejecutar la prueba

Desde la terminal, en el directorio raíz del proyecto:

```powershell
# Activar entorno virtual
.\.venv\Scripts\Activate.ps1

# Ejecutar el script de prueba
python -m src.typack_etl.transformer.transformer_midnight_split
```

### 2. Resultado

El script:
1. ✅ Lee el archivo consolidado original
2. ✅ Identifica registros que cruzan medianoche
3. ✅ Los divide en dos registros
4. ✅ Recalcula producción proporcionalmente
5. ✅ Recalcula `Duracion(min)`
6. ✅ Recalcula `Minutos_Extras` (si aplica)
7. ✅ Recalcula `Periodo_Laboral` (si aplica)
8. ✅ Valida que la suma de producción se mantenga
9. ✅ Guarda un archivo de prueba: `mt_typ_consolidated_midnight_split_TEST.csv`

### 3. Validaciones automáticas

El script verifica:
- ✅ No hay registros con `inicio > fin`
- ✅ La suma de producción total se mantiene exacta
- ✅ Muestra ejemplos antes y después de la división

## Archivos generados

- **Ubicación**: Carpeta `consolidated` definida en `config.json`
- **Archivo de prueba**: `mt_typ_consolidated_midnight_split_TEST.csv`
- **Archivo original**: `mt_typ_consolidated.csv` (sin modificar)

## Integración con el transformer principal

Si deseas integrar esta funcionalidad al proceso principal de transformación:

1. Importa la función en `transformer.py`:
```python
from transformer_midnight_split import split_midnight_crossings, recalculate_minutos_extras
```

2. Aplica después de `transform_data()` en el método `process_line()`:
```python
# Aplicar transformaciones
monthly_df = self.transform_data(monthly_df)

# Dividir registros que cruzan medianoche
monthly_df = split_midnight_crossings(monthly_df)
monthly_df = recalculate_minutos_extras(monthly_df, self)
```

## Casos especiales manejados

- ✅ Registros que cruzan múltiples días (se dividen en múltiples segmentos)
- ✅ Producción = 0 o NULL (se mantiene en 0)
- ✅ Duración = 0 (no divide, evita división por cero)
- ✅ Actualización de columnas `Mes` y `Año` cuando cambia el día
- ✅ Preservación de todos los demás campos del registro

## Consideraciones

- El archivo de prueba usa **separador `;`** y **decimal `,`** (formato español)
- La codificación es **UTF-8 con BOM** para compatibilidad con Excel
- Los redondeos se hacen a **2 decimales** para producción y duración
- El último tramo garantiza que la suma sea **exacta** (sin error de redondeo)

## Soporte

Para dudas o problemas, revisa:
1. Los logs de consola al ejecutar el script
2. El archivo de prueba generado
3. El código fuente en `transformer_midnight_split.py`
