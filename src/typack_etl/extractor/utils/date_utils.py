"""Utilidades de fecha del extractor, alineadas al día operativo.

Tres relojes
------------
La API **recibe** los parámetros en UTC y **devuelve** los timestamps en el
reloj de su propio servidor. Ese reloj es hora chilena, pero cambia de hora
con las reglas que Chile usó **entre 2016 y 2018** (verano del 2º domingo de
agosto al 2º domingo de mayo), no con las vigentes desde 2019 (1er sábado de
septiembre al 1er sábado de abril). La planta, en cambio, trabaja en hora
local real: los turnos empiezan a las 08:00/18:00/22:00 todo el año.

  UTC          -> lo que se le pide a la API (--start-date, --end-date)
  reloj API    -> lo que traen las filas (start, end). Es el espacio en que se
                  compara, particiona y audita, porque la identidad de un
                  segmento sale de su `start`/`end` tal como los manda el
                  proveedor (ver `audit.keys.record_key`).
  hora local   -> `America/Santiago` con las reglas vigentes. Es el reloj de
                  los turnos y el único en que las reglas de negocio horarias
                  son correctas.

Los dos relojes coinciden la mayor parte del año y difieren en una hora en las
dos ventanas en que uno ya cambió y el otro no:

    1er domingo de abril  -> 2º domingo de mayo        (reloj API +1 h)
    2º domingo de agosto  -> 1er domingo de septiembre (reloj API +1 h)

Verificado sobre 340.055 filas de 2024-2026: los cortes de turno saltan de
08:00 a 09:00 exactamente en esas cuatro fechas, todos los años. El campo
`turn` que manda la propia API es el testigo, porque el proveedor lo asigna
con la hora real de la planta: `scripts/verificar_horario.py` lo contrasta
contra la hora y detecta cualquier corrimiento.

Consecuencia: `a_local()` es obligatorio antes de aplicar cualquier regla de
turno, y el corte del día operativo son las 08:00 **locales**, que en UTC son
las 11:00 en verano y las 12:00 en invierno. Ninguno de los dos desfases es
una constante: los dos se derivan de la fecha del propio dato, nunca de la
fecha en que corre el ETL. Un timestamp del 1-sep se convierte con las reglas
que regían el 1-sep aunque se lo reprocese en diciembre.

Día operativo
-------------
El negocio no trabaja en días de calendario sino en turnos de 24 h que van de
corte a corte: las 08:00 locales, que es el cambio de turno TD/TN. Por eso un
registro del día 1 a las 06:00 pertenece al día —y al mes— operativo anterior.
"""

from datetime import date, datetime, time, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

# Formatos que devuelve la API en los campos start/end.
API_DATETIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")

# Zona local de la planta: la de los turnos y la de las reglas de negocio.
TZ_PLANTA = ZoneInfo("America/Santiago")

# El servidor del proveedor sí cambia de hora, pero con las reglas que Chile
# usó entre 2016 y 2018 (verano del 2º domingo de agosto al 2º domingo de
# mayo), no con las vigentes desde 2019. Es una base de zonas horarias vieja.
_DST_API_INICIO_MES = 8   # 2º domingo de agosto: el reloj del proveedor adelanta
_DST_API_FIN_MES = 5      # 2º domingo de mayo:   el reloj del proveedor atrasa

# Hora estándar del proveedor, la que rige fuera de su verano.
_API_ESTANDAR = timedelta(hours=-4)

# Cambio de turno TD/TN de config/horarios.json, en hora local. Es la
# definición del corte del día operativo, y la única forma en que el corte
# está escrito en alguna parte: su expresión en UTC o en el reloj del
# proveedor se calcula para cada fecha en vez de configurarse.
CORTE_LOCAL = time(8, 0)

_CORTE_DESDE_MEDIANOCHE = timedelta(
    hours=CORTE_LOCAL.hour, minutes=CORTE_LOCAL.minute, seconds=CORTE_LOCAL.second
)


def _segundo_domingo(year: int, month: int) -> date:
    """Segundo domingo de un mes, que es cuando cambia el reloj del proveedor."""
    primero = date(year, month, 1)
    return primero + timedelta(days=(6 - primero.weekday()) % 7 + 7)


def api_en_verano(momento: datetime) -> bool:
    """¿El reloj del proveedor está en horario de verano en ese instante?

    Su verano va del 2º domingo de agosto al 2º domingo de mayo, así que cruza
    el fin de año: es verdadero desde agosto hasta mayo del año siguiente.
    """
    dia = momento.date()
    return dia >= _segundo_domingo(dia.year, _DST_API_INICIO_MES) or dia < _segundo_domingo(dia.year, _DST_API_FIN_MES)


class _RelojAPI(tzinfo):
    """El reloj del servidor del proveedor, como zona horaria.

    Existe para que la conversión dependa de la fecha del dato y no de una
    constante de configuración. Mientras el desfase fue un número en el `.env`
    había que moverlo cuatro veces al año, y cualquier reproceso convertía los
    datos viejos con el desfase de hoy: en la corrida del 11-sep-2026, cinco
    segmentos del 1-sep se archivaron en septiembre cuando por turno (`TN`,
    arrancado el 31-ago) pertenecían a agosto.

    La hora estándar es −4 todo el año y el verano suma una hora, que es la
    forma que espera el `fromutc()` heredado: descuenta primero el offset
    estándar y recién sobre la hora ya local evalúa el DST.
    """

    def utcoffset(self, dt: datetime | None) -> timedelta:
        return _API_ESTANDAR + self.dst(dt)

    def dst(self, dt: datetime | None) -> timedelta:
        if dt is None:
            return timedelta(0)
        return timedelta(hours=1) if api_en_verano(dt) else timedelta(0)

    def tzname(self, dt: datetime | None) -> str:
        return "API-03" if dt is not None and api_en_verano(dt) else "API-04"


RELOJ_API = _RelojAPI()


def reloj_api(momento: datetime) -> timezone:
    """Desfase fijo del reloj del proveedor vigente en ese instante (−3 o −4)."""
    return timezone(_API_ESTANDAR + RELOJ_API.dst(momento))


def relojes_desfasados(momento: datetime) -> bool:
    """¿El reloj del proveedor va adelantado respecto de la hora local?

    Verdadero sólo dentro de las dos ventanas del año en que las reglas de DST
    del proveedor y las del país no coinciden. Sirve para reportar por régimen
    en vez de por estación, que es lo que hace visible el problema.
    """
    return momento.replace(tzinfo=TZ_PLANTA).utcoffset() != reloj_api(momento).utcoffset(None)


# ---------------------------------------------------------------------------
# Conversión entre espacios
# ---------------------------------------------------------------------------

def a_planta(dt_utc: datetime) -> datetime:
    """UTC -> reloj del proveedor (ambos naive), con las reglas de esa fecha."""
    return dt_utc.replace(tzinfo=timezone.utc).astimezone(RELOJ_API).replace(tzinfo=None)


def a_utc(dt_planta: datetime) -> datetime:
    """Reloj del proveedor -> UTC (ambos naive), con las reglas de esa fecha."""
    return dt_planta.replace(tzinfo=RELOJ_API).astimezone(timezone.utc).replace(tzinfo=None)


def a_local(dt_api: datetime) -> datetime:
    """Reloj del proveedor -> hora local de planta (ambos naive).

    Los dos relojes son hora chilena, pero con reglas de DST distintas, así que
    coinciden la mayor parte del año y difieren en una hora en las dos ventanas
    en que el proveedor ya cambió y el país todavía no, o al revés:

        1er domingo de abril  -> 2º domingo de mayo        (proveedor +1 h)
        2º domingo de agosto  -> 1er domingo de septiembre (proveedor +1 h)

    Fuera de esas ~9 semanas al año la conversión no mueve nada. Aplicar un
    desfase fijo, en cambio, corrompería los 10 meses que hoy están bien.

    Es obligatoria antes de razonar sobre turnos, horas de término o días de la
    semana, que son reglas de reloj local.
    """
    return dt_api.replace(tzinfo=RELOJ_API).astimezone(TZ_PLANTA).replace(tzinfo=None)


def a_api(dt_local: datetime) -> datetime:
    """Hora local de planta -> reloj del proveedor. Inversa de `a_local`.

    Hace falta para ir del negocio al dato: el corte y los bordes del día
    operativo se definen en hora local, pero hay que compararlos contra
    timestamps que vienen en el reloj del proveedor.
    """
    return dt_local.replace(tzinfo=TZ_PLANTA).astimezone(RELOJ_API).replace(tzinfo=None)


def utc_a_local(dt_utc: datetime) -> datetime:
    """UTC -> hora local de planta (ambos naive).

    El atajo que evita pasar por el reloj del proveedor cuando lo que se está
    preguntando es de calendario —en qué día operativo estamos— y no tiene
    nada que ver con lo que el proveedor cree que hora es.
    """
    return dt_utc.replace(tzinfo=timezone.utc).astimezone(TZ_PLANTA).replace(tzinfo=None)


def corte_utc_esperado(dia: date) -> time:
    """El corte que le corresponde a `dia`: las 08:00 locales llevadas a UTC.

    11:00 en el verano del país y 12:00 en invierno.
    """
    local = datetime.combine(dia, CORTE_LOCAL, tzinfo=TZ_PLANTA)
    return local.astimezone(timezone.utc).time()


def corte_utc(dia: date | None = None) -> time:
    """Hora UTC del corte del día operativo `dia`.

    Depende de la fecha, así que quien la llame debería pasar el día del que
    habla; sin argumento contesta por hoy, que es lo que quiere una corrida en
    vivo. Antes era una constante del `.env` que había que mover dos veces al
    año.
    """
    return corte_utc_esperado(dia or now_local().date())


def now_utc() -> datetime:
    """Ahora en UTC, naive, para comparar contra los cortes."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def now_planta() -> datetime:
    """Ahora en el reloj del proveedor: el mismo espacio que los datos."""
    return a_planta(now_utc())


def now_local() -> datetime:
    """Ahora en hora local de planta: el reloj de los turnos y del calendario."""
    return datetime.now(TZ_PLANTA).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Día y mes operativo
# ---------------------------------------------------------------------------

def dia_operativo(dt_planta: datetime) -> date:
    """Día operativo al que pertenece un timestamp del reloj del proveedor.

    El día operativo D va desde las 08:00 locales del día D hasta las 08:00
    locales del D+1, así que un registro del 1-abr 06:00 pertenece al 31-mar.

    El corte se aplica **después** de pasar a hora local, nunca antes: dentro
    de las ventanas de desfase el reloj del proveedor va una hora adelantado, y
    aplicarle el corte local directamente corre el borde del día.
    """
    return (a_local(dt_planta) - _CORTE_DESDE_MEDIANOCHE).date()


def mes_operativo(dt_planta: datetime) -> tuple[int, int]:
    """(año, mes) operativo al que pertenece un timestamp del proveedor."""
    d = dia_operativo(dt_planta)
    return d.year, d.month


def inicio_dia_operativo(dia: date) -> datetime:
    """Instante del reloj del proveedor en que empieza el día operativo `dia`."""
    return a_api(datetime.combine(dia, CORTE_LOCAL))


def inicio_mes_operativo(year: int, month: int) -> datetime:
    """Instante del reloj del proveedor en que empieza el mes operativo."""
    return inicio_dia_operativo(date(year, month, 1))


def minutos_dia_operativo(dia: date) -> float:
    """Cuánto dura el día operativo `dia`, en minutos de tiempo real.

    Son 1440 casi siempre, pero 1380 o 1500 en los dos días del año en que el
    país cambia la hora: el día operativo va de 08:00 locales a 08:00 locales,
    y esa noche tiene 23 o 25 horas. Es el denominador de la cobertura, que si
    se fija en 1440 alerta por una caída que nunca ocurrió.
    """
    # Los dos bordes se pasan a UTC antes de restar: entre dos datetime que
    # comparten el mismo `tzinfo`, Python resta el reloj de pared e ignora el
    # cambio de hora, que es justo lo que acá hay que medir.
    inicio = datetime.combine(dia, CORTE_LOCAL, tzinfo=TZ_PLANTA).astimezone(timezone.utc)
    fin = datetime.combine(dia + timedelta(days=1), CORTE_LOCAL, tzinfo=TZ_PLANTA).astimezone(timezone.utc)
    return (fin - inicio).total_seconds() / 60


# ---------------------------------------------------------------------------
# Parseo y formato
# ---------------------------------------------------------------------------

MONTH_NAMES_ES = {
    1: "enero", 2: "febrero", 3: "marzo", 4: "abril",
    5: "mayo", 6: "junio", 7: "julio", 8: "agosto",
    9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre",
}


def month_name_es(month_num: int) -> str:
    """Convierte número de mes (1-12) a nombre en español."""
    return MONTH_NAMES_ES.get(month_num, str(month_num))


def parse_api_datetime(value) -> datetime | None:
    """Convierte un `start`/`end` de la API a datetime naive.

    Retorna None si el valor está vacío o no se puede interpretar, para que
    quien llame decida qué hacer en vez de asumir una fecha.
    """
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if not value:
        return None
    text = str(value).strip()
    for fmt in API_DATETIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def fmt_api(dt: datetime) -> str:
    """Formato de fecha/hora que espera la API (espacio UTC)."""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def validar_formato_fecha(fecha_str):
    """
    Valida que una cadena tenga el formato esperado: 'AAAA-MM-DD HH:MM:SS'.
    Retorna True si es válido, False en caso contrario.
    """
    try:
        datetime.strptime(fecha_str, "%Y-%m-%d %H:%M:%S")
        return True
    except ValueError:
        return False
