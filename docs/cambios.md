# Instrucciones para Agente de IA: Cortar registros que cruzan medianoche y balancear producción

## Objetivo
Dada una lista de registros de sensores con `inicio`, `fin`, `producción` y `turno`, debes **dividir (cortar) cualquier registro que cruce medianoche** (cambio de día) en **dos registros**:
1) Desde `inicio` hasta el final del día (23:59).
2) Desde el inicio del día siguiente (00:00) hasta `fin`.

Luego debes **repartir la producción proporcionalmente al tiempo trabajado** en cada tramo, de modo que la suma de producciones resultantes sea exactamente igual a la producción original.

---

## Entrada esperada
Una colección (tabla/lista/CSV/JSON) de registros con estas columnas/campos mínimos:

- `inicio`: fecha y hora de inicio (ej. `01-01-2025 22:30`)
- `fin`: fecha y hora de término (ej. `02-01-2025 04:33`)
- `producción`: número (entero o decimal) asociado al periodo
- `turno`: texto (ej. `TN`)

> Asume que `inicio < fin` y que las fechas/hora están en la misma zona horaria.

---

## Reglas de transformación

### 1) Detectar si el registro cruza medianoche
Un registro cruza medianoche si:
- la **fecha de `inicio`** es distinta a la **fecha de `fin`**.

Si no cruza medianoche, **no hagas cambios**.

---

### 2) Crear los dos nuevos registros cuando cruza medianoche
Para un registro que cruza medianoche:

- Registro A:
  - `inicio_A = inicio_original`
  - `fin_A = (fecha de inicio) 23:59`
- Registro B:
  - `inicio_B = (fecha de fin) 00:00`
  - `fin_B = fin_original`
- `turno` se mantiene igual en ambos registros.

> Nota: Mantén el formato de fecha/hora de la entrada.

---

### 3) Repartir producción proporcional al tiempo
Define duraciones en minutos (o segundos, pero sé consistente) para evitar errores:

- `dur_total = fin_original - inicio_original`
- `dur_A = fin_A - inicio_A`
- `dur_B = fin_B - inicio_B`

Producción proporcional (sin redondeo aún):
- `prod_A_raw = producción_original * (dur_A / dur_total)`
- `prod_B_raw = producción_original * (dur_B / dur_total)`

---

### 4) Redondeo y conservación exacta de la suma
Si la producción debe ser **entera** (como en el ejemplo), aplica esta política:

1. `prod_A = redondear(prod_A_raw)` (redondeo estándar a entero, .5 hacia arriba).
2. `prod_B = producción_original - prod_A`

Esto garantiza que:
- `prod_A + prod_B == producción_original` siempre.

Si la producción puede ser decimal, conserva decimales con una precisión fija (ej. 2 o 3) y ajusta el último registro para que la suma coincida exactamente con el original.

---

### 5) Orden de salida
- Reemplaza el registro original por los dos nuevos.
- Mantén el orden cronológico por `inicio`.

---

## Validaciones obligatorias
Después de transformar:
- Ningún registro resultante debe tener `inicio > fin`.
- `dur_total == dur_A + dur_B`
- `producción_original == prod_A + prod_B`
- `turno` se conserva.
- No pierdas ni dupliques registros (solo se reemplaza el que cruza medianoche).

---

## Ejemplo

### Registro original
| inicio            | fin               | producción | turno |
|------------------|-------------------|-----------:|:-----|
| 01-01-2025 22:30 | 02-01-2025 04:33  | 4300       | TN   |

### Resultado esperado
| inicio            | fin               | producción | turno |
|------------------|-------------------|-----------:|:-----|
| 01-01-2025 22:30 | 01-01-2025 23:59  | 2131       | TN   |
| 02-01-2025 00:00 | 02-01-2025 04:33  | 2169       | TN   |

> La producción queda balanceada proporcionalmente al tiempo trabajado y la suma se mantiene: 2131 + 2169 = 4300.

---

## Casos borde que debes manejar
- Registros que empiezan exactamente a `00:00` (no requiere corte si fin es mismo día).
- Registros que terminan exactamente a `23:59` (no requiere corte si inicio es mismo día).
- Duraciones muy cortas (asegurar que el cálculo no genere división por cero; si `dur_total == 0`, reportar error o mantener sin cambios según política).
- Si el registro cruza más de un día (ej. 3 días): **aplica cortes por cada medianoche**, generando múltiples segmentos diarios y repartiendo la producción proporcionalmente (mismo criterio de ajuste en el último segmento para conservar suma exacta).

---

## Entregable del agente
Devuelve la lista de registros transformada en el mismo formato que la entrada (tabla/CSV/JSON), manteniendo columnas originales y valores actualizados.