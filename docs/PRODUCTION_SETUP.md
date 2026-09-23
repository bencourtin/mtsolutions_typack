# Puesta en producción — ETL Typack

Procedimiento para dejar el ETL corriendo solo, todos los días, en un servidor
Windows de la empresa.

> **Esta versión reemplaza a la anterior por completo.** La vieja describía una
> configuración por `config/config.json` y `config/email_config.json` que el
> código ya no lee, y mandaba a instalar con un `requirements.txt` de la raíz que
> fue eliminado. Si tenés impresa o guardada la versión en PDF, descartala.

---

## 1. Requisitos del servidor

| | |
|---|---|
| Sistema | Windows (Server o 10/11 Pro) |
| Python | 3.10 o superior — en desarrollo corre 3.13 |
| Git | Para clonar y actualizar |
| Google Drive for Desktop | En **modo Reflejar** (ver §2) |
| Red | Salida HTTPS a `mts2.mtsolutions.io` y SMTP al relay de correo |
| Cuenta | Una cuenta de servicio con sesión propia de Drive y permisos sobre la carpeta compartida |

---

## 2. Google Drive en modo Reflejar

**Es el paso que hace o rompe todo el despliegue.** Vale la pena entender por qué.

Google Drive for Desktop ofrece dos modos:

- **Transmitir** (por defecto): los archivos viven en la nube y se exponen como
  una unidad virtual `G:`. Esa unidad se monta **por sesión de usuario**.
- **Reflejar**: los archivos son copias reales en el disco local, en una carpeta
  del perfil, y Drive las sincroniza en segundo plano.

Una tarea programada configurada como *"ejecutar aunque el usuario no haya
iniciado sesión"* corre en la sesión 0, sin escritorio interactivo. En modo
Transmitir, **esa sesión no ve `G:`**, y el ETL falla sin encontrar ni los datos
ni las rutas de salida. Es el error clásico de este tipo de despliegue.

En modo Reflejar los archivos existen en el sistema de archivos local, así que la
tarea los abre como cualquier otro archivo.

**Qué hacer:**

1. Instalar Google Drive for Desktop e iniciar sesión con la cuenta de servicio.
2. Preferencias → Google Drive → **Reflejar archivos**.
3. Sincronizar la unidad compartida que contiene `Data_MTSolutions` y la carpeta
   del BI.
4. Anotar la ruta local resultante. Suele ser algo como
   `C:\Users\<cuenta>\My Drive\...` o `C:\Users\<cuenta>\<Unidad compartida>\...`.
5. Esas rutas locales son las que van en el `.env` del paso 4. **No usar `G:`.**

> Si por alguna razón el modo Reflejar no es viable, el plan B es una cuenta con
> inicio de sesión automático y la tarea configurada como *"ejecutar sólo cuando
> el usuario haya iniciado sesión"*. Funciona, pero depende de que la sesión
> siga abierta: si alguien cierra sesión, el ETL deja de correr en silencio.

---

## 3. Instalación

```powershell
cd D:\ETL
git clone https://github.com/bencourtin/mtsolutions_typack.git
cd mtsolutions_typack

py -3 -m pip install --target vendor -r requirements\requirements.txt
```

> **Usar `py -3`, no `python` a secas.** En muchas instalaciones de Windows
> `python` resuelve al alias-stub de Microsoft Store
> (`WindowsApps\python.exe`), que no es un intérprete real: sale con "no se
> encontró Python" y código `9009` sin ejecutar nada. `py` es el lanzador
> oficial (`py.exe`, instalado junto con Python desde python.org) y no cae en
> esa trampa. Todos los comandos de este documento, y los `.bat` de `deploy/`,
> usan `py -3` por eso.

**No se usa `venv`.** Las dependencias de terceros quedan instaladas en
`vendor\`, una carpeta propia del repo, con el Python del sistema. Lo que hace
importables `typack_etl`, `config` y `scripts` no es una instalación (`pip
install -e .`) sino `PYTHONPATH`: hay que apuntarlo a `vendor`, `src` y la raíz
del repo, en ese orden. Los lanzadores (`scripts\*.bat`) ya lo arman solo; para
correr algo a mano hay que armarlo también:

```powershell
$env:PYTHONPATH = "$PWD\vendor;$PWD\src;$PWD"
```

Cada verificación y comando de este documento asume que `PYTHONPATH` ya está
seteado así en la sesión. `requirements/requirements.txt` es la fuente real de
las dependencias acá — a diferencia del desarrollo local, donde
`pyproject.toml` alimenta un `pip install -e .` sobre un `.venv`.

---

## 4. Configuración (`.env`)

```powershell
copy .env.example .env
notepad .env
```

Todo se configura en ese archivo, en la raíz del repo. No hay JSON de
configuración: `config/settings.py` lee de `.env` y nada más.

### Lo que hay que completar sí o sí

| Variable | Qué es |
|---|---|
| `API_USER`, `API_PASS` | Credenciales de la API de MT Solutions |
| `SMTP_USERNAME`, `SMTP_PASSWORD` | Cuenta de envío (contraseña de aplicación) |
| `EMAIL_RECIPIENTS_ERROR` | Quiénes reciben las alertas, separados por coma |
| `EMAIL_RECIPIENTS_WEEKLY` | Quiénes reciben el reporte semanal |
| Las ocho rutas `PATH_*` | Ver abajo |

Si falta cualquiera de las primeras cuatro, el ETL arranca igual pero o no
autentica contra la API, o falla en silencio al avisar de un problema.

### Las rutas

Las ocho tienen que estar definidas y apuntar a la carpeta **local reflejada** de
Drive. Una ruta que se omita cae a un default relativo al repositorio, y ahí el
problema no se ve hasta que duele:

```
PATH_RAW              Particiones por mes operativo
PATH_PROCESSED        Salida del transformer
PATH_CONSOLIDATED     Lo que lee Power BI (¡es otra carpeta, la del BI!)
PATH_STREAM_DATA      Sin uso hoy
PATH_LOGS             Bitácoras, estadísticas y el lock de corrida
PATH_SNAPSHOTS        Respuestas crudas de la API
PATH_AUDIT            Libro de reasignaciones y cobertura diaria
```

> **Ojo con `PATH_AUDIT` y `PATH_SNAPSHOTS`.** En el entorno de desarrollo estaban
> sin definir, así que el libro de auditoría se venía escribiendo dentro del
> propio repositorio. Eso es estado crítico: si queda fuera de Drive, un
> `git clone` nuevo lo pierde y la historia de reasignaciones arranca de cero.

### Lo que ya no hay que tocar cuatro veces al año

Hasta el 2026-09-16 el corte del día operativo (`CORTE_OPERATIVO_UTC`) y el
desfase del reloj del proveedor (`OFFSET_PLANTA_HORAS`) eran constantes del
`.env` que **dependían de la fecha**, y entre las dos había que editarlas en
cuatro fechas al año. Ya no existen: los dos valores se derivan de la fecha de
cada dato en `date_utils`, con la zona horaria del proveedor (`RELOJ_API`,
reglas de DST anteriores a 2019) y la del país (`America/Santiago`).

No vuelva a agregarlas al `.env`: ya no se leen, y tenerlas ahí hace creer que
mover el valor cambia algo.

El modelo horario queda vigilado por dos cosas. Dentro de cada corrida,
`audit/reloj.py` contrasta el `duration` de cada segmento contra el intervalo:
si el reloj del proveedor salta donde el modelo no lo espera, la línea sale
`RELOJ_INESPERADO` y la corrida imprime `ALERTA DE RELOJ`. Está calibrado sobre
las 347.478 filas de 2024-2026 con cero falsos positivos.

Fuera de la corrida, `scripts/verificar_horario.py` hace la comprobación
independiente contra el campo `turn`, sobre todo el histórico:

    py -3 scripts\verificar_horario.py --raw

### Lo que casi nunca hay que tocar

`EXTRACTOR_WINDOW_DAYS`, `RETENCION_LOGS_DIAS`, `RETENCION_SNAPSHOTS_DIAS`,
`ETL_MAX_LINEAS_FALLIDAS`. Los defaults están razonados en
`config/settings.py` y en [`ARQUITECTURA_ETL.md`](ARQUITECTURA_ETL.md).

---

## 5. Verificación, en este orden

No programar nada hasta que los tres pasos den bien.

```powershell
REM 1. La suite completa, sin red y sin tocar producción
py -3 -m pytest

REM 2. Que el correo salga
py -3 scripts\run_daily_etl.py --test-email

REM 3. Una corrida diaria completa, a mano
py -3 scripts\run_daily_etl.py
```

El paso 3 tiene que terminar con código 0 y dejar
`mt_typ_consolidated.estado.json` en estado `OK` junto al consolidado.

---

## 6. Tareas programadas

Las definiciones importables están en [`deploy/`](../deploy/README.md), con el
procedimiento de sustitución de marcadores e importación con `schtasks`.

Resumen: la diaria a las 08:30 con límite de 4 h, la semanal los lunes a las
09:00. Ambas con *"no iniciar una nueva instancia si ya está en ejecución"*.

**Después de importar, correr la tarea a mano una vez** (`schtasks /run`) y
confirmar que termina bien. Es la única forma de comprobar que la cuenta de la
tarea ve las rutas de Drive sin sesión interactiva. Si falla acá y anda a mano,
el problema es el del §2.

---

## 7. Recarga histórica

Se hace **una sola vez**, después de que el ETL diario ya funciona, para
reemplazar la base vieja por una escrita con el esquema auditable.

```powershell
REM 1. Deshabilitar la tarea diaria mientras dure
schtasks /change /tn "ETL Typack - Diario" /disable

REM 2. Ver el plan sin tocar nada
py -3 scripts\recarga_historica.py --dry-run

REM 3. Vaciar el destino (hay respaldo previo en Data_MTSolutions)
REM    raw\ y processed\ completos

REM 4. Recargar
py -3 scripts\recarga_historica.py

REM 5. Reprocesar TODO, sin --incremental
py -3 -m src.typack_etl.transformer.transformer

REM 6. Rehabilitar
schtasks /change /tn "ETL Typack - Diario" /enable
```

Tres cosas que conviene saber antes de arrancar:

- **Vaciar `raw/` no es higiene, es corrección.** Si se recarga encima de los
  archivos viejos, el CDC compara contra ellos y la traza marca `reasignada=1` en
  miles de filas que sólo cambiaron de formato — y eso viaja al consolidado que
  lee Power BI. Por eso el script se niega a arrancar sobre un destino sucio.
- **Vaciar `processed/` tampoco es opcional.** Ahí conviven procesados de 28, 29 y
  34 columnas; la consolidación los concatena sin alinear, así que un reproceso
  parcial deja el consolidado con columnas mezcladas.
- **El reproceso va sin `--incremental`.** El incremental decide por fecha de
  modificación, y la sincronización de Drive puede alterarlas.

La recarga es reanudable: si se corta, volver a lanzarla saltea los meses que ya
cerraron bien. Al terminar, **descartar el libro desechable**
(`<PATH_LOGS>\recarga\_audit`): el libro real tiene que nacer limpio en la primera
corrida diaria, de modo que el histórico queda auditable hacia adelante.

---

## 8. Qué mirar cuando algo va mal

De más a menos detalle:

| Dónde | Qué cuenta |
|---|---|
| `<PATH_LOGS>\scheduler\etl_YYYYMMDD.log` | La bitácora del ETL. Rota a los `RETENCION_LOGS_DIAS` días. |
| `<PATH_LOGS>\results_<marca>.csv` | Una fila por línea: filas traídas y estado (`OK`, `SIN_DATOS`, `COBERTURA_BAJA`, `TRUNCADO_SOSPECHOSO`, `RELOJ_INESPERADO`, `ERROR`) |
| `logs\launcher\daily_YYYYMMDD.log` | Local al repo. El último recurso: si falla antes de que Python arranque, el motivo está sólo acá. |
| `<PATH_CONSOLIDATED>\mt_typ_consolidated.estado.json` | Si lo que ve Power BI salió de una corrida completa o parcial |
| Visor de eventos → Programador de tareas | Si la tarea ni siquiera disparó |

### Síntomas frecuentes

**La tarea termina con error pero a mano anda.** Es el problema de Drive del §2:
la cuenta de la tarea no ve las rutas. Verificar que Drive esté en modo Reflejar
y que el `.env` no mencione `G:`.

**"No se encontraron las dependencias".** El `.bat` busca `vendor\` en la raíz
del repo (instalado con `pip install --target vendor`, sin venv). Falta
instalar las dependencias, o el repositorio se movió.

**Código `9009` / "no se encontró Python".** El `.bat` no encontró un
intérprete real vía `py -3` (ver la nota de §3). Confirmar que el Python
Launcher (`py.exe`) esté instalado y en el `PATH` de la cuenta que corre la
tarea — no alcanza con que ande para el usuario interactivo si la tarea corre
bajo otra cuenta o sin sesión.

**`ModuleNotFoundError` al correr un comando a mano.** Falta `PYTHONPATH`. Los
`.bat` lo arman solos; para correr algo a mano hay que setearlo primero (ver
§3): `$env:PYTHONPATH = "$PWD\vendor;$PWD\src;$PWD"`.

**El ETL sale con código 2.** No es un error: había otra corrida en curso y esta
se omitió. Si se repite todos los días, quedó un lock huelgo en
`<PATH_LOGS>\etl.lock`; se descarta solo a las 5 h.

**No llegan correos y tampoco hay error.** Los fallos de SMTP no tumban el ETL,
pero desde esta versión quedan registrados como `ERROR` en la bitácora. Buscar
"No se pudo enviar la alerta".

**El ETL no transformó.** Si fallan más líneas que `ETL_MAX_LINEAS_FALLIDAS`, se
aborta a propósito para no publicar un consolidado incompleto. El detalle de qué
línea falló está en el `results_*.csv`.

---

## 9. Comandos útiles

```powershell
REM Estadísticas de la semana (sólo lectura)
py -3 scripts\stats_tracker.py

REM Reporte semanal a mano
py -3 scripts\run_daily_etl.py --weekly-report

REM Una línea puntual, ventana a medida
py -3 -m src.typack_etl.extractor.pipelines.extractor_30m --devid 1273 --days 60

REM Actualizar el código en el servidor
git pull
py -3 -m pip install --target vendor -r requirements\requirements.txt --upgrade
py -3 -m pytest
```

---

## 10. Riesgo conocido

El estado —libro de auditoría, cobertura, `ultimas_corridas.json`— vive en CSV
sobre una carpeta sincronizada por Drive, sin bloqueo a nivel de archivo. El lock
de corrida única protege contra dos procesos en el mismo servidor, pero no contra
la sincronización asíncrona ni contra alguien editando esos archivos desde otro
equipo.

La recomendación de fondo sigue siendo mover el estado a SQLite o DuckDB local al
servidor y publicar a Drive sólo lo que Power BI lee. Está anotado en
[`ARQUITECTURA_ETL.md`](ARQUITECTURA_ETL.md) y queda fuera de este despliegue.
