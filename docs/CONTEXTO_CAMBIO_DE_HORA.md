# Contexto: el cambio de hora y el reloj del proveedor

> Estado al **2026-09-16**. Trabajo hecho el 9, 10, 11 y 16 de septiembre de
> 2026. Este archivo existe para retomar el tema en otra sesión sin volver a
> investigarlo. Lo que ya quedó en la documentación permanente está en
> [`ARQUITECTURA_ETL.md`](ARQUITECTURA_ETL.md) §2.bis y en
> [`PRODUCTION_SETUP.md`](PRODUCTION_SETUP.md).
>
> El §3.2 se cerró el 16-sep: ya no hay constantes de hora en el `.env`. Lo
> disparó [`TEST_VENTANA_30D_20260911.md`](TEST_VENTANA_30D_20260911.md).

---

## 1. El hallazgo

El ETL asumía que los timestamps que devuelve la API estaban en un **desfase
fijo** respecto de UTC (`OFFSET_PLANTA_HORAS = -4`, documentado como "el
proveedor mantiene los horarios cuadrados, no sigue el horario de verano").

Es falso. El servidor del proveedor **sí cambia de hora, pero con las reglas
que Chile usó entre 2016 y 2018** (verano del 2º domingo de agosto al 2º
domingo de mayo), no con las vigentes desde 2019 (1er sábado de septiembre al
1er sábado de abril). La planta trabaja en hora local real, con turnos que
empiezan siempre a las 08:00/18:00/22:00.

Resultado: los dos relojes coinciden ~43 semanas al año y **el reloj de la API
va una hora adelantado durante unas 9**:

| Fecha | Qué pasa | Quién cambia |
|---|---|---|
| 1er domingo de abril | el reloj API queda **+1 h** | el país atrasa (regla 2019+) |
| 2º domingo de mayo | se alinean | atrasa el proveedor (regla 2016-2018) |
| 2º domingo de agosto | el reloj API queda **+1 h** | adelanta el proveedor (regla 2016-2018) |
| 1er domingo de septiembre | se alinean | el país adelanta (regla 2019+) |

En 2026 esas fechas fueron 5-abr, 10-may, 9-ago y 6-sep. En 2027: 4-abr,
9-may, 8-ago y 5-sep.

### Cómo se verificó

La API entrega dos cosas redundantes por segmento: el timestamp (reloj del
proveedor) y el campo `turn` (que el proveedor asigna con la hora real de
planta). Contrastar uno contra otro es la única forma de auditar el modelo
temporal desde fuera. Sobre **340.055 filas de 2024-2026**:

| | coincidencia `turn` vs hora |
|---|---|
| con `a_local()` (regla correcta) | **99,99 %** |
| sin corregir, dentro de las ventanas de desfase | ~90 % |
| corrigiendo todo el invierno (hipótesis descartada) | ~85 % en junio-julio |

Además, la línea de tiempo diaria de los bordes de turno salta de 08:00 a
09:00 exactamente en esas cuatro fechas, los tres años.

### El tercer testigo: `duration` contra `end - start` (16-sep)

Hay una segunda redundancia que confirma el modelo sin depender de `turn`. En
un segmento normal `duration` coincide con la diferencia de reloj entre `end` y
`start`; si el reloj del proveedor salta **dentro** del segmento, no. Sobre
342.849 segmentos de 2024-2026 hay **58 que difieren, todos por exactamente
±60 min**, y todos caen en la noche de una transición del proveedor:

```
2025-08-09 22:00 -> 2025-08-10 09:00   duration=600  reloj=660   (adelantó)
2025-05-10 23:00 -> 2025-05-11 08:00   duration=600  reloj=540   (atrasó)
```

La geometría del bloque confirma el modelo por sí sola: en agosto va de 22:00 a
09:00 del reloj del proveedor y en mayo de 23:00 a 08:00, que en los dos casos
son las 22:00 y las 08:00 **locales** — los bordes TT/TN y TN/TD.

### Por qué no importa la hora exacta del salto

`api_en_verano()` decide por fecha, así que asume que el proveedor cambia a
medianoche. Si en realidad cambiara a las 04:00, las filas de esa madrugada
quedarían con el desfase anterior.

No existen esas filas. En los seis domingos de transición de 2024-2026,
**ninguna fila arranca entre las 00:00 y las 08:00**: la planta reporta esa
noche como un único bloque que la cruza entera. La ambigüedad no es observable
y no puede afectar a ningún dato.

### El error que hay que no repetir

La primera conclusión —"el reloj es fijo UTC−3"— salió de un sample del 10-ago
al 8-sep, que cae **entero dentro de una ventana de desfase**. Cualquier
muestra de menos de un año da un modelo equivocado. Corregir "todo el
invierno" habría roto mayo, junio y julio, que estaban bien.

---

## 2. Lo que ya está hecho (en el working tree, sin commitear)

| Archivo | Cambio |
|---|---|
| `.env`, `.env.example`, `config/settings.py` | **16-sep**: se retiraron `OFFSET_PLANTA_HORAS` y `CORTE_OPERATIVO_UTC`; ya no se configuran |
| `src/typack_etl/extractor/utils/date_utils.py` | `a_local()`, `api_en_verano()`, `reloj_api()`, `relojes_desfasados()`, `corte_utc_esperado()`, `CORTE_LOCAL`, `TZ_PLANTA`. **16-sep**: `RELOJ_API` (tzinfo), `a_api()`, `utc_a_local()`, `minutos_dia_operativo()`; `dia_operativo()`, `a_planta()`, `a_utc()` y `corte_utc()` pasaron a ser date-aware |
| `src/typack_etl/extractor/pipelines/extractor_30m.py` | **16-sep**: `_auto_range()` alinea cada borde al corte de su propio día |
| `src/typack_etl/audit/ledger.py` | **16-sep**: los bordes del día operativo salen del calendario, no de sumar 24 h; la cobertura se mide contra la duración real del día |
| `src/typack_etl/transformer/transformer.py` | `_a_hora_local()` en la frontera de entrada (línea ~222) y día operativo con corte plano 08:00 local |
| `scripts/verificar_horario.py` | **nuevo**: criterio de aceptación y detector permanente |
| `tests/test_fechas.py`, `tests/test_recarga.py`, `tests/test_transformer.py` | casos de las cuatro transiciones; el corte ya no se fija como constante |
| `docs/ARQUITECTURA_ETL.md`, `docs/EXTRACTOR_USAGE.md`, `docs/PRODUCTION_SETUP.md`, `deploy/README.md` | modelo temporal corregido |

Efecto inmediato del punto 1: la corrida diaria volvió a cerrar la ventana el
mismo día. Desde el 6-sep cerraba en el corte de *ayer* y el turno de noche
recién cerrado entraba 24 h tarde.

**351 tests en verde** (`.venv\Scripts\python -m pytest tests\ -q`).

---

## 3. Lo que quedó pendiente

### 3.1 Publicar el reproceso del histórico (decisión del usuario)

El consolidado que alimenta el BI sigue con los períodos de desfase corridos
una hora. Medido el 10-sep:

```
producción hoy : 2024 +1h 93,58 %   2025 +1h 90,17 %   2026 +1h 90,32 %
reprocesado    : 99,99 % en todos los períodos
```

Impacto del reproceso: **43.599 filas** se corren 1 h (12,5 % del total) y de
ésas **1.755 cambian de día operativo**. El total de filas no cambia
(347.486). No cambia ninguna de mes.

El staging que se generó vivía en el scratchpad de la sesión y **ya no está**;
se regenera en un par de minutos. Para revisarlo antes de publicar:

```powershell
.venv\Scripts\python -m typack_etl.transformer.transformer `
    --input "<PATH_RAW>" --processed "<tmp>\proc" --consolidated "<tmp>\cons"
.venv\Scripts\python scripts\verificar_horario.py --ruta "<tmp>\cons\mt_typ_consolidated.csv"
```

Para publicar (reescribe `PATH_PROCESSED` y el `mt_typ_consolidated.csv` del
BI — **hacer respaldo antes**, en esa carpeta ya hay varios `*_backup_*.csv`):

```powershell
.venv\Scripts\python -m typack_etl.transformer.transformer   # sin --incremental
.venv\Scripts\python scripts\verificar_horario.py            # debe dar >= 99,5 %
```

### 3.2 Automatizar las cuatro ediciones anuales del `.env` — HECHO el 2026-09-16

`CORTE_OPERATIVO_UTC` y `OFFSET_PLANTA_HORAS` ya no existen. El desfase del
reloj del proveedor es ahora una `tzinfo` (`date_utils.RELOJ_API`) con las
reglas de DST anteriores a 2019, y el corte se deriva de `CORTE_LOCAL` (08:00)
con la zona del país. Todo se calcula con la fecha del dato, así que ya no hay
ediciones anuales ni reproceso que convierta datos viejos con el desfase de
hoy.

Lo disparó el test de la ventana de 30 días del 11-sep, que encontró cinco
segmentos del 1-sep archivados en septiembre cuando por turno pertenecían a
agosto: ver [`TEST_VENTANA_30D_20260911.md`](TEST_VENTANA_30D_20260911.md).

### 3.3 Confirmar con el proveedor

Vale la pena reportarles que su servidor usa la tabla de zonas horarias
anterior a 2019, y pedir que los timestamps vengan con offset explícito
(ISO 8601). Es el arreglo de raíz: todo el modelo se sostiene en inferencia
sobre los datos.

**Mientras tanto, la corrida lo vigila sola (16-sep).** `audit/reloj.py`
compara el `duration` de cada segmento contra el intervalo convertido con
nuestro modelo. Si el proveedor mueve su reloj donde no lo esperamos —o deja
de moverlo donde sí—, la cuenta no cierra por exactamente una hora: la línea
sale `RELOJ_INESPERADO` y la corrida imprime `ALERTA DE RELOJ` con el
diagnóstico. No aborta el ETL a propósito (ver `ESTADOS_DE_AVISO` en
`run_daily_etl`).

Calibrado sobre las 347.478 filas de 2024-2026 con intervalo utilizable:
**cero desajustes**, incluidos los 58 segmentos que cruzan un salto del reloj.
Sin falsos positivos conocidos, así que si suena, algo cambió.

---

## 4. Reglas que no hay que romper

- **El raw no se corrige.** Guarda lo que el proveedor dijo. La identidad de un
  segmento en el libro de auditoría es `sha1(devid + start + end)` con esos
  valores (`audit/keys.py`): reescribirlo re-keyearía todo el histórico, dejaría
  huérfano `detenciones_audit.csv` y haría que cada corrida viera todas las
  filas como `NUEVO`. La conversión va en la frontera del transformer.
- **No aplicar la corrección "todo el invierno"**: rompe mayo, junio y julio.
- **El disparador del Programador de tareas (08:30) no se mueve** con el cambio
  de hora: corre en hora local y el corte son las 08:00 locales, así que se
  mueven juntos. Desde el 16-sep tampoco se mueve el `.env`.
- **El corte y el desfase se derivan de la fecha del dato, nunca de la fecha de
  la corrida.** Es lo que hace que un reproceso en diciembre dé el mismo
  resultado que la corrida del día. Volver a ponerlos como constante reintroduce
  el corrimiento de partición del 11-sep.
- Quedan ~43 filas de EXT_14/15/16 con `turn=TT` empezando después de las
  22:00. Es ruido del borde TT/TN de esas líneas, no tiene relación con la hora;
  por eso el umbral de `verificar_horario.py` es 99,5 % y no 100 %.

---

## 5. Comandos útiles

```powershell
# Detector permanente: ¿el reloj del proveedor sigue donde creemos?
.venv\Scripts\python scripts\verificar_horario.py --raw

# Estado del consolidado que publica el BI
.venv\Scripts\python scripts\verificar_horario.py

# ¿El .env le corresponde a hoy?
.venv\Scripts\python -c "from typack_etl.extractor.utils.date_utils import advertencias_configuracion as a; print(a() or 'sin avisos')"

.venv\Scripts\python -m pytest tests\ -q
```
