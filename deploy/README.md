# Tareas programadas del ETL Typack

Definiciones importables de Task Scheduler, para no depender de veinte clics
manuales ni de que alguien recuerde marcar las casillas correctas.

El procedimiento completo de instalación del servidor está en
[`docs/PRODUCTION_SETUP.md`](../docs/PRODUCTION_SETUP.md). Acá sólo van las tareas.

## Antes de importar

Los XML traen dos marcadores que hay que reemplazar por los valores del servidor:

| Marcador | Reemplazar por |
|---|---|
| `__PROJECT_DIR__` | Ruta del repositorio, ej. `D:\ETL\mtsolutions_typack` |
| `__USUARIO__` | Cuenta que va a correr el proceso, ej. `TYPACK\svc_etl` |

Desde PowerShell, parados en la raíz del repo:

```powershell
$proyecto = (Get-Location).Path
$usuario  = "TYPACK\svc_etl"

Get-ChildItem deploy\*.xml | ForEach-Object {
    $destino = "$env:TEMP\$($_.Name)"
    (Get-Content $_ -Raw -Encoding Unicode).
        Replace("__PROJECT_DIR__", $proyecto).
        Replace("__USUARIO__", $usuario) |
        Set-Content $destino -Encoding Unicode
    Write-Output "listo: $destino"
}
```

> Los archivos están en UTF-16 (Unicode) porque es lo que `schtasks /create /xml`
> espera. Convertirlos a UTF-8 hace que la importación falle.

## Importar

```cmd
schtasks /create /xml "%TEMP%\ETL_Typack_Diario.xml"  /tn "ETL Typack - Diario"          /ru TYPACK\svc_etl /rp *
schtasks /create /xml "%TEMP%\ETL_Typack_Semanal.xml" /tn "ETL Typack - Reporte Semanal" /ru TYPACK\svc_etl /rp *
```

`/rp *` pide la contraseña de forma interactiva, para no dejarla en el historial
de comandos.

## Qué queda configurado

| | Diario | Semanal |
|---|---|---|
| Disparador | Todos los días 08:30 | Lunes 09:00 |
| Acción | `scripts\run_daily_etl.bat` | `scripts\run_weekly_report.bat` |
| Límite de ejecución | 4 h | 30 min |
| Si ya está corriendo | No inicia otra instancia | ídem |
| Si se perdió el horario | Corre en cuanto pueda | ídem |
| Reintentos | 2, cada 30 min | — |
| Requiere red | Sí | Sí |

Las 4 h del límite diario dan margen sobre los 3 h de timeouts internos del
propio ETL (1 h de extractor + 2 h de transformer).

### Por qué el diario dispara a las 08:30 y no antes

La hora del disparador **decide qué día alcanza la ventana**, y por eso no se
puede mover libremente. El extractor cierra la ventana en el último corte
operativo ya cumplido: las 08:00 de planta, el cambio de turno TD/TN, que en
UTC son las 11:00 en verano y las 12:00 en invierno. Nunca la cierra en un corte futuro, porque incluiría un
turno de noche a medio andar.

La consecuencia es que un disparador **anterior a las 08:00 termina la ventana
en las 08:00 del día anterior**:

    corre 06:00 planta  ->  ventana termina AYER 08:00     (le falta el turno de noche que acaba de cerrar)
    corre 08:30 planta  ->  ventana termina HOY  08:00     (correcto)

Estuvo en 06:00 hasta septiembre de 2026: el turno de noche recién entraba a la
extracción del día siguiente. Los 30 min de margen sobre las 08:00 cubren la
holgura del Programador de tareas y que el corte es exacto.

Si se cambia esta hora, tiene que quedar **después** del corte de planta.

El disparador **no** hay que moverlo con el horario de verano: corre en hora
local de Windows y el corte son las 08:00 locales, así que los dos se mueven
juntos. Desde el 2026-09-16 tampoco hay que tocar el `.env`: el corte y el
desfase del reloj del proveedor se calculan por fecha, no se configuran — ver
[`PRODUCTION_SETUP.md`](../docs/PRODUCTION_SETUP.md), "Lo que ya no hay que
tocar cuatro veces al año".

`No inicia otra instancia` es el segundo cerrojo: el primero es el lock que toma
el propio `run_daily_etl.py`, que además protege contra una recarga histórica
lanzada a mano mientras la tarea corre.

## Verificar

```cmd
schtasks /query /tn "ETL Typack - Diario" /v /fo LIST
schtasks /run   /tn "ETL Typack - Diario"
```

El resultado de una corrida queda en tres lugares, de más a menos detalle:

1. `<PATH_LOGS>\scheduler\etl_YYYYMMDD.log` — la bitácora del ETL.
2. `logs\launcher\daily_YYYYMMDD.log` — local al repo, es el último recurso: si
   falla antes de que Python arranque, el motivo aparece sólo acá.
3. `<PATH_CONSOLIDATED>\mt_typ_consolidated.estado.json` — si lo que publicó el
   BI salió de una corrida completa o parcial.

## Códigos de salida

| Código | Significado |
|---|---|
| 0 | Corrida completa |
| 1 | Falló la extracción o la transformación (sale alerta por correo) |
| 2 | Ya había otra corrida en curso; esta se omitió. **No es un error.** |

## Quitar las tareas

```cmd
schtasks /delete /tn "ETL Typack - Diario" /f
schtasks /delete /tn "ETL Typack - Reporte Semanal" /f
```

Conviene deshabilitar la diaria —no borrarla— mientras corre la recarga
histórica: `schtasks /change /tn "ETL Typack - Diario" /disable`.
