# Test de la ventana de 30 días: qué cambió en las detenciones

> Extracción del **2026-09-11** comparada contra la data de prueba del
> **2026-09-09**, para medir qué reasigna el proveedor y con cuánto retraso.
> El test encontró además un defecto de particionado, arreglado el
> **2026-09-16** (§4). Contexto del modelo horario:
> [`CONTEXTO_CAMBIO_DE_HORA.md`](CONTEXTO_CAMBIO_DE_HORA.md).

---

## 1. Qué se corrió

```powershell
# sandbox: copia de la data de prueba, para que el CDC compare contra ella
Copy-Item -Recurse data\prueba_20260909 data\prueba_20260911

.venv\Scripts\python -m typack_etl.extractor.pipelines.extractor_30m --days 30 --no-progress `
    --output     data\prueba_20260911\raw `
    --logs-dir   data\prueba_20260911\logs `
    --audit-dir  data\prueba_20260911\audit `
    --snapshots-dir data\prueba_20260911\snapshots
```

Las cuatro rutas van al sandbox a propósito: sin ellas el extractor escribe en
`PATH_RAW`, que es Drive.

| | |
|---|---|
| Ventana | `2026-08-12 11:00` → `2026-09-11 11:00` UTC (08:00 → 08:00 de planta) |
| Líneas | 11, todas `OK` |
| Segmentos | 11.872 recibidos: 1.447 nuevos, 10.425 ya conocidos |

---

## 2. Lo que cambió el proveedor en dos días

Tres cambios reales sobre 10.425 segmentos ya conocidos.

| Tipo | Línea | Segmento | Cambio |
|---|---|---|---|
| REASIGNACIÓN | TF_8 | 08-sep 18:01:31 → 18:09:04 (7,55 min) | `No asignado` → `01.- Planificado - Inventario/sin orden de producción`; `code_f`: No planificado → Planificado. Lo asignó LUIS FLORES **3 días** después del evento |
| RECORTE | EXT_15 | arranca 08-sep 09:49:42 | fin `08-sep 23:03:23` → `09-sep 01:47:39` (**+164,3 min**) |
| RECORTE | EXT_16 | arranca 07-sep 23:32:34 | fin `08-sep 23:01:51` → `09-sep 07:39:10` (**+517,3 min**) |

Los dos recortes son detenciones que seguían abiertas cuando se extrajo el
09-sep y se cerraron después: no son ediciones, son el cierre tardío del
segmento. En el tramo 12-ago → 07-sep no cambió **nada**.

Esto es lo que justifica la ventana móvil: con el extractor mensual, una
reasignación de tres días de retraso sobre un segmento del día 28 no se
volvería a consultar nunca.

### Un falso positivo que el CDC descartó bien

Comparando los CSV fila a fila aparece un cuarto cambio en TF_3 (25-ago
09:41:07): dos segmentos de duración cero en el mismo instante, uno con
`Micro parada` y otro vacío, que la API devolvió en orden distinto en cada
corrida. No es un cambio. El CDC los compara como grupo (`cdc._grupo_equivalente`)
y no reportó nada, que es lo correcto. Cualquier script de comparación que
indexe por `record_key` en un diccionario los va a pisar y va a inventar una
reasignación.

---

## 3. El último día operativo siempre llega incompleto

En las dos extracciones, la última fila de cada línea termina a las 23:00 y
**falta el bloque TN** (23:00 → 08:00, ~540 min por línea).

```
cobertura por día operativo, corrida del 11-sep:
  2026-08-12 .. 2026-09-09    100,0 %   (29 días)
  2026-09-10                   61,8 %   (mínima 54,2 %)
```

Medido sobre el 08-sep, entre lo que vio la corrida del 09-sep y lo que
realmente había: **382 → 563 filas (+47 %), 8.244 → 15.151 min (+84 %)**. La
extracción del 11-sep corrió 6 h 50 después de que cerrara ese turno y el dato
todavía no estaba.

No es un defecto del ETL: es latencia del proveedor, y la ventana móvil lo
repara sola en la corrida siguiente (el replay del 16-sep muestra el 09-10 ya
al 100 %). Lo que importa es la consecuencia aguas abajo: **el último día de
cualquier corrida es provisorio y el BI no debería leerlo como cerrado.**

---

## 4. El defecto que destapó: el día operativo se calculaba con la fecha de hoy

### Síntoma

La corrida registró 5 `DESAPARECIDO` + 5 `REAPARECIDO` de las mismas claves.
Eran falsos: cinco filas del **1-sep entre 08:31 y 08:44** se movieron del
archivo de agosto al de septiembre.

```
TF_9   08:31:48  local 07:31:48  turn=TN   agosto -> septiembre
TF_9   08:34:18  local 07:34:18  turn=TN   agosto -> septiembre
EXT_15 08:34:50  local 07:34:50  turn=TN   agosto -> septiembre
TF_2   08:42:27  local 07:42:27  turn=TN   agosto -> septiembre
TF_2   08:44:17  local 07:44:17  turn=TN   agosto -> septiembre
```

### Causa

`dia_operativo()` aplicaba el corte **antes** de pasar a hora local, y ese
corte salía de `CORTE_OPERATIVO_UTC`, una constante del `.env` con el valor
vigente **el día de la corrida**.

El 1-sep caía dentro de una ventana de desfase (9-ago → 6-sep), donde el reloj
del proveedor va +1 h: el corte correcto en ese reloj eran las 09:00, no las
08:00 de hoy. El `turn=TN` que manda el propio proveedor confirma que los cinco
segmentos pertenecen al turno de noche que arrancó el 31-ago, o sea a agosto.

La corrida del 09-sep, con el corte de entonces (12:00 UTC), los tenía bien. La
del 11-sep los movió mal. Las dos usaban el mismo código: el resultado dependía
de en qué estación se corriera.

### Arreglo (2026-09-16)

`CORTE_OPERATIVO_UTC` y `OFFSET_PLANTA_HORAS` **ya no existen**. Es el §3.2 de
`CONTEXTO_CAMBIO_DE_HORA.md`, cerrado.

| Archivo | Cambio |
|---|---|
| `date_utils.py` | `RELOJ_API`, una `tzinfo` con las reglas de DST que Chile usó entre 2016 y 2018. `a_planta()`, `a_utc()`, `a_local()` y el nuevo `a_api()` van contra ella, así que dependen de la fecha del dato |
| `date_utils.dia_operativo()` | convierte a hora local **primero** y recién ahí aplica `CORTE_LOCAL` (08:00), igual que ya hacía el transformer |
| `date_utils.corte_utc(dia)` | date-aware; sin argumento contesta por hoy |
| `date_utils.minutos_dia_operativo(dia)` | 1440, o 1380/1500 en los dos cambios de hora del país |
| `extractor_30m._auto_range()` | cada borde se alinea al corte de su propio día, en vez de restar 24 h por día |
| `extractor_mensual`, `recarga_historica` | el mes operativo usa el corte de cada borde |
| `ledger` | los bordes del día operativo salen del calendario, no de sumar 24 h; la cobertura se mide contra la duración real del día |
| `config/settings.py`, `.env`, `.env.example` | se retiraron las dos constantes |
| `extractor_30m` | se retiró el `AVISO DE CONFIGURACIÓN`: ya no hay configuración que pueda quedar atrás |

Se retiraron también `corte_planta()` y `advertencias_configuracion()`, que
sólo existían para sostener las constantes.

### Alcance: el BI nunca estuvo afectado

El transformer ya convertía con `a_local()` y recalculaba el día operativo, así
que la partición del raw no llegaba a la salida. Verificado transformando el
raw antes y después del arreglo:

```
claves comunes:                              12.391
filas que el fix movió de día/mes/turno:          0
mes operativo agosto:  7.237 filas, 355.865,40 min  (idéntico)
```

Lo que costaba el defecto era ~10 filas falsas en `detenciones_audit.csv` por
cada transición, cuatro veces al año, más el churn de archivos.

### Verificación

Replay del mismo rango del 11-sep, sobre un sandbox limpio, ya con el arreglo:

```
Cambios: 30 reasignaciones, 2 otras ediciones, 2 recortes,
         0 desaparecidos, 0 reaparecidos
Estado por línea: OK=11   (cobertura 100,0 % en las 11)
```

Los cinco segmentos del 1-sep quedan en la partición de agosto. Las 30
reasignaciones son actividad real del proveedor acumulada entre el 11 y el
16-sep.

`verificar_horario.py --raw` sobre la descarga nueva: **99,96 %** (11.882 de
11.887). Las 5 discrepancias son el ruido conocido del borde TT/TN de EXT_16.
**351 tests en verde.**

### Un efecto secundario, esperado

Al corregir los bordes del día operativo, los dos días del año en que el país
cambia la hora pasan a medir 23 o 25 horas en vez de 24. Antes se contaba dos
veces la hora del cambio y el día cerraba en 1440 min falsos.

La primera corrida después del arreglo va a ver bajar el 2026-09-05 de 1440 a
1380 min en todas las líneas. **No es una pérdida de datos**, es la corrección
del doble conteo: `registrar_cobertura` no la reporta como caída porque el día
está reportando su jornada completa.

---

## 5. Higiene

- `data/prueba_20260909/raw/2026/09/produccion_2026_septiembre_TF_2 - copia.csv`
  duplica 27 filas al transformar y corre los días 01/02/03-sep hasta 1.550
  min. Se excluyó del comparativo; conviene borrarlo.
- El bloque de comentarios del `.env` explicaba la hipótesis vieja y descartada
  ("el reloj del proveedor es FIJO en UTC-3"). Corregido junto con el retiro de
  las constantes.

---

## 6. Cómo repetir el test

```powershell
# 1. sandbox desde la última data de prueba
Copy-Item -Recurse data\prueba_<AAAAMMDD> data\prueba_<HOY>
Remove-Item "data\prueba_<HOY>\raw\2026\09\*copia*.csv"

# 2. extraer contra el sandbox (el CDC compara contra lo que ya hay)
.venv\Scripts\python -m typack_etl.extractor.pipelines.extractor_30m --days 30 --no-progress `
    --output data\prueba_<HOY>\raw --logs-dir data\prueba_<HOY>\logs `
    --audit-dir data\prueba_<HOY>\audit --snapshots-dir data\prueba_<HOY>\snapshots

# 3. el modelo horario sigue en pie?
.venv\Scripts\python scripts\verificar_horario.py --raw --ruta data\prueba_<HOY>\raw

# 4. qué cambió: filas del libro con el run_id de esta corrida
#    (tipo_cambio, campo, valor_anterior -> valor_nuevo, dias_desde_evento)
Import-Csv data\prueba_<HOY>\audit\detenciones_audit.csv -Delimiter ';' |
    Where-Object run_id_actual -like '<stamp>*' | Format-Table -AutoSize
```

Para medir el impacto en el BI, transformar los dos raw a un temporal y
comparar `mt_typ_consolidated.csv` por `Record_Key`; nunca contra
`PATH_CONSOLIDATED`.

Señales a mirar, en orden:

1. `desaparecidos` y `reaparecidos` de la **misma clave en la misma corrida**:
   siempre es un problema de particionado, nunca del proveedor.
2. Cobertura por día: 100 % en todos menos el último. Si baja otro día, la API
   dejó de reportar algo.
3. `verificar_horario.py --raw` por debajo de 99,5 %: el proveedor movió su
   reloj otra vez.
