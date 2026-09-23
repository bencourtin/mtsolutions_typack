"""Clasificación de cada registro en su turno, según `config/horarios.json`.

Qué decide el turno
-------------------
La **hora de inicio**, siempre, y en hora local de planta (ver
`date_utils.a_local`: el reloj del proveedor va una hora adelantado unas nueve
semanas al año, y clasificar con él corre los bordes de turno). Un registro que
empieza en un turno y termina en otro queda entero en el turno donde empezó: no
se fragmenta.

La vigencia
-----------
`horarios.json` trae una lista de vigencias, cada una con su `vigencia_desde`.
A una fila le corresponde la vigencia **vigente más reciente que no sea
posterior a su fecha**. No hay una vigencia por defecto: una fila anterior a la
más antigua se queda sin horario aplicable, y eso se reporta en vez de
adivinarse (ver `Caso.SIN_VIGENCIA`).

La `temporada` de cada vigencia es una etiqueta descriptiva: no participa del
cálculo. Lo que selecciona es la fecha.

Las reglas que no salen del JSON
--------------------------------
1. **No se sobrescribe lo que ya vino del origen.** Si la fila trae `TD`, `TT`
   o `TN`, ese valor manda por sobre el cálculo. El cálculo entra sólo cuando
   el origen no sabe: `T0`, `-` o vacío. Medido sobre 46.397 registros de
   mayo-septiembre 2026, el origen y el config discrepan en 656 filas; la
   mayoría son el solape de Extrusión, donde el config es ambiguo y el dato no.

2. **El hueco de Termoformado es turno Día.** El área sólo tiene TD y TN
   habilitados y entre el fin de TD y el inicio de TN queda una franja sin
   turno. Es la única excepción que sí pisa lo que trajo el origen. No se
   resuelve moviendo el fin de TD en el JSON porque ese horario lo comparte
   Extrusión, donde la franja pertenece a TT.

   El hueco **no es una constante**: se deriva del JSON para cada día y cada
   vigencia, porque el viernes la jornada es corta. Con la vigencia de invierno
   2026 va de 18:00 a 23:00 de lunes a jueves, y de 15:00 a 21:30 el viernes.

   Ojo con la asimetría: para **clasificar**, el Día llega hasta que empieza la
   noche; para **contar horas extra**, el Día termina a la hora que dice el
   JSON. Por eso `fin_de_turno()` no aplica esta regla.

3. **El solape TD/TT de Extrusión** se resuelve a favor de TD cuando no hay
   dato de origen. Es lo que hace el propio proveedor: de las 822 filas que
   caen en el solape y traen turno, las 822 dicen TD. El solape cambia según el
   día (lunes de invierno 14:30-18:00, martes a jueves 13:00-18:00, viernes
   13:00-15:00), así que se calcula, no se fija.

4. **Sábado y domingo no están en el JSON.** Las filas de fin de semana sin
   turno de origen se clasifican como TD y **nunca** suman minutos extra, por
   decisión del 2026-09-16. Se cuentan aparte para que la corrida lo informe.
"""

from __future__ import annotations

import unicodedata
from datetime import date, datetime, time, timedelta
from typing import Mapping

# Lo que el origen manda cuando no sabe qué turno es.
SIN_DATO = frozenset({"", "T0", "-", "0", "NONE", "NAN"})

# El turno al que van el hueco de Termoformado y el fin de semana sin horario.
# Usado por `clasificar()` (una fila = un turno, no se fragmenta). El motor de
# fragmentación (`Horarios.tramos()`, más abajo) usa TNL para lo mismo: ver su
# docstring para por qué las dos reglas conviven.
TURNO_DEL_HUECO = "TD"
TURNO_FIN_DE_SEMANA = "TD"
AREA_CON_HUECO = "Termoformado"

# Turno No Laboral: lo que le corresponde a un tramo fragmentado (`tramos()`)
# que cae fuera de cualquier franja de `turnos_permitidos` (fin de semana, el
# hueco entre turnos) o que el comentario del registro marca como feriado. No
# es un turno de `horarios.json` — no tiene franja horaria propia — así que
# `franja_vigente("TNL", ...)` y `ventana_turno("TNL", ...)` devuelven `None`
# sin necesidad de código especial: es exactamente el comportamiento que
# necesitan `Hora_Termino_Turno` y `Periodo_Laboral` para un tramo no laboral.
TNL = "TNL"

_AREAS_DATOS = {"TERMOFORMADO": "Termoformado", "EXTRUSION": "Extrusion"}

# La única palabra que hoy marca un feriado en los datos reales (verificado en
# Extrusión, agosto 2026): vive en `Comentarios`, no en `Desc_detencion`. Un
# feriado puede traer un turno ya declarado por el proveedor (TD/TT/TN, no
# sólo T0), así que la detección no depende de `SIN_DATO`.
_TOKEN_FERIADO = "FERIADO"

# Los días como los nombra el JSON: sin tildes y en minúscula.
DIAS = ("lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo")


class Caso:
    """Por qué quedó el turno que quedó. Sirve para informar y para las extras."""

    ORIGEN = "origen"                  # lo trajo el proveedor, no se tocó
    CALCULADO = "calculado"            # salió del horario de la vigencia
    HUECO = "hueco"                    # franja sin turno de Termoformado
    FIN_DE_SEMANA = "fin_de_semana"    # sábado o domingo: el JSON no los define
    SIN_VIGENCIA = "sin_vigencia"      # fecha anterior a la vigencia más antigua
    SIN_AREA = "sin_area"              # la línea no mapea a un área del config

    # Casos propios de `Horarios.tramos()` (fragmentación por turno).
    NO_CUBIERTO = "no_cubierto"        # tramo fuera de toda franja: hueco o fin de semana -> TNL
    FERIADO = "feriado"                # comentario marca feriado: pisa el turno ya declarado -> TNL


def _sin_tildes(texto) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", str(texto or ""))
        if unicodedata.category(c) != "Mn"
    ).upper().strip()


def normalizar_area(linea) -> str | None:
    """El área del JSON que le corresponde a la columna `Linea` de los datos.

    Los datos traen `EXTRUSIÓN` con tilde y el config `Extrusion` sin: sin
    normalizar, el área no se encuentra y toda Extrusión quedaría sin clasificar.
    """
    return _AREAS_DATOS.get(_sin_tildes(linea))


def es_feriado(comentario) -> bool:
    """True si `comentario` (columna `Comentarios`) marca un feriado.

    `horarios.json` no contempla feriados -- no hay campo para eso -- así que
    esto es lo único que los distingue de un día laboral común. Tolerante a
    `None`/`NaN`/`pd.NA`: `Comentarios` está vacío en la enorme mayoría de las
    filas, y no vale la pena acoplar este módulo a pandas sólo para el chequeo
    de nulos (evita además el `TypeError` de `pd.NA` en un `or`).
    """
    if comentario is None:
        return False
    texto = str(comentario).strip()
    if not texto or texto.lower() in ("nan", "<na>", "none"):
        return False
    return _TOKEN_FERIADO in _sin_tildes(texto)


def _hora(texto) -> time:
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(str(texto).strip(), fmt).time()
        except ValueError:
            continue
    raise ValueError(f"hora no interpretable en horarios.json: {texto!r}")


def _dentro(momento: time, inicio: time, fin: time) -> bool:
    """`inicio <= momento < fin`, tratando el cruce de medianoche.

    Un turno que termina antes de empezar (TN 23:00-08:00) abarca el tramo
    final del día más el inicial del siguiente.
    """
    if inicio <= fin:
        return inicio <= momento < fin
    return momento >= inicio or momento < fin


class Vigencia:
    """Un juego de horarios y la fecha desde la que rige."""

    def __init__(self, cfg: Mapping):
        self.id = cfg.get("id", "")
        self.temporada = cfg.get("temporada", "")
        self.desde = date.fromisoformat(cfg["vigencia_desde"])
        self.comentario = cfg.get("comentario", "")
        self.horarios = {
            turno: {
                _sin_tildes(dia).lower(): (_hora(f["inicio"]), _hora(f["fin"]))
                for dia, f in por_dia.items()
            }
            for turno, por_dia in cfg["horarios"].items()
        }

    def franja(self, turno: str, dia: str) -> tuple[time, time] | None:
        return self.horarios.get(turno, {}).get(dia)

    def __repr__(self) -> str:
        return f"<Vigencia {self.id} desde {self.desde}>"


class Horarios:
    """Los horarios de `config/horarios.json`, listos para clasificar."""

    def __init__(self, cfg: Mapping):
        self.cfg = cfg
        # De la más reciente a la más antigua: la primera que no sea posterior
        # a la fecha buscada es la que rige. Se incluyen también las vigencias
        # `vigente: false`: el flag no decide qué vigencia aplica a una fecha,
        # sólo si esa vigencia sigue rigiendo *hoy*. Una vigencia vieja marcada
        # `vigente: false` puede seguir siendo la que corresponde a una fecha
        # histórica que cayó dentro de su rango -- antes de que la siguiente
        # (por `vigencia_desde`) la reemplazara -- y filtrarla acá la dejaría
        # sin horario aplicable aunque el JSON sí lo tenga.
        self.vigencias = sorted(
            (Vigencia(v) for v in cfg["vigencias"]),
            key=lambda v: v.desde,
            reverse=True,
        )
        self._permitidos = {
            area: tuple(v["turnos_permitidos"]) for area, v in cfg["areas"].items()
        }

    # -- vigencia ---------------------------------------------------------

    def vigencia_para(self, momento) -> Vigencia | None:
        """La vigencia que rige en esa fecha, o None si es anterior a todas."""
        if momento is None:
            return None
        dia = momento.date() if isinstance(momento, datetime) else momento
        for vigencia in self.vigencias:
            if vigencia.desde <= dia:
                return vigencia
        return None

    # -- consulta ---------------------------------------------------------

    def permitidos(self, area: str) -> tuple:
        """Turnos habilitados en un área. Sin área conocida, ninguno."""
        return self._permitidos.get(area, ())

    def franja(self, turno: str, momento: datetime) -> tuple[time, time] | None:
        """(inicio, fin) del turno para la vigencia y el día de `momento`.

        El día es el de inicio del turno: el TN del viernes usa el horario del
        viernes aunque termine el sábado. Devuelve None si no hay vigencia o si
        el día no está definido (sábado y domingo).

        Sólo mira el día propio de `momento`. Para resolver una fila cuyo
        `start` ya cayó en el día calendario siguiente al que arrancó su
        turno —el caso del TN del viernes, que sigue corriendo el sábado de
        madrugada y sábado no tiene horario propio en el JSON— hace falta
        `franja_vigente()`.
        """
        vigencia = self.vigencia_para(momento)
        if vigencia is None or not isinstance(momento, datetime):
            return None
        return vigencia.franja(turno, DIAS[momento.weekday()])

    def franja_vigente(self, turno: str, momento: datetime) -> tuple[time, time] | None:
        """`franja()`, pero sigue el turno un día atrás si hace falta.

        El TN del viernes cruza medianoche y sábado no tiene horario propio en
        el JSON, así que `franja("TN", <sábado de madrugada>)` da `None` aunque
        el turno ya se conozca (por origen: `clasificar()` no lo toca). Sin
        esto, cualquier fila cuyo `start` cayó del otro lado de la medianoche
        pierde su horario de referencia: hora de término, minutos extra y
        período laboral quedan en blanco para el tramo de un turno de noche
        que es exactamente el que más importa medir.

        Ojo con un caso más sutil: si HOY sí define el turno pero `momento`
        cae antes de que arranque (su propia madrugada, no su noche), la
        franja propia de hoy no sirve para esa madrugada -- pertenece, si
        acaso, a la ocurrencia de AYER, con los horarios de AYER. Confundir
        las dos importa cuando un día cambia de horario respecto al anterior
        (el viernes suele arrancar más temprano que el resto de la semana):
        usar los números de hoy para describir la cola de ayer arma una
        ventana que no corresponde a ningún turno real declarado, y que en el
        peor de los casos no avanza -- ver `Horarios.tramos()`, que depende de
        que esto nunca devuelva una ventana que ya terminó.

        Si el día propio de `momento` no define el turno, o lo define pero
        `momento` está en su propia madrugada, se prueba el día anterior: si
        ese horario cruza medianoche (`termina <= arranca`) y la hora de
        `momento` cae antes de `termina`, es la cola de ese turno -- no uno
        nuevo que hoy no tiene horario. Un turno que no cruza medianoche
        (TD, TT) nunca activa este camino.
        """
        if not isinstance(momento, datetime):
            return None
        propia = self.franja(turno, momento)
        if propia is not None:
            arranca, termina = propia
            if arranca <= termina or momento.time() >= arranca:
                return propia
            # Cruza medianoche y `momento` todavía no llegó a `arranca`: es
            # la propia madrugada de hoy, no la noche. Sigue a `anterior`.
        anterior = self.franja(turno, momento - timedelta(days=1))
        if anterior is None:
            return None
        arranca, termina = anterior
        if termina <= arranca and momento.time() < termina:
            return anterior
        return None

    def ventana_turno(self, turno: str, momento: datetime) -> tuple[datetime, datetime] | None:
        """Inicio y fin del turno que rige en `momento`, con fecha y todo.

        A diferencia de `franja()`/`franja_vigente()`, que sólo dan horas del
        día, esto resuelve a qué día calendario pertenece cada extremo: si
        `momento` está en la franja vespertina en que arranca su propio turno,
        el fin cae al día siguiente (turno que cruza medianoche); si está en la
        madrugada, es el inicio el que quedó ayer. Necesario para no adivinar
        mal cuando el turno lo trae el origen y el día de `momento` no tiene
        horario propio (el TN del viernes, ya en sábado).
        """
        franja = self.franja_vigente(turno, momento)
        if franja is None or not isinstance(momento, datetime):
            return None
        arranca, termina = franja
        cruza_medianoche = termina <= arranca
        if cruza_medianoche and momento.time() < arranca:
            # Madrugada: la cola de un turno que arrancó el día calendario
            # anterior (con su propio horario, o con el de un día sin
            # definición propia que "prestó" `franja_vigente`).
            inicio_dt = datetime.combine(momento.date() - timedelta(days=1), arranca)
            fin_dt = datetime.combine(momento.date(), termina)
        else:
            inicio_dt = datetime.combine(momento.date(), arranca)
            fin_dt = datetime.combine(momento.date(), termina)
            if cruza_medianoche:
                fin_dt += timedelta(days=1)
        return inicio_dt, fin_dt

    def fecha_inicio_turno(self, turno: str | None, momento: datetime) -> date | None:
        """La fecha en que arrancó el turno que contiene `momento`, no la de `momento`.

        `horarios.json` no define sábado ni domingo, así que un turno real
        nunca puede arrancar esos días: uno que sigue corriendo ahí (el TN del
        viernes, entrado el sábado de madrugada) le pertenece igual al día en
        que arrancó. Usa `ventana_turno()` -- ya resuelve exactamente esto,
        vía `franja_vigente()` -- en vez de `momento.date()` directo, que daría
        el día de la fila de datos y no el del turno.

        Para `TNL` o cuando no hay turno que resolver (área o vigencia
        desconocida), no hay "arranque de turno" que perseguir: la fecha es
        la del propio tramo.
        """
        if not isinstance(momento, datetime):
            return None
        if turno is None or turno == TNL:
            return momento.date()
        ventana = self.ventana_turno(turno, momento)
        if ventana is None:
            return momento.date()
        return ventana[0].date()

    def hueco(self, momento: datetime) -> tuple[time, time] | None:
        """La franja sin turno de Termoformado ese día: del fin de TD al inicio de TN.

        Se deriva del JSON en vez de fijarse, porque cambia con el día y con la
        vigencia. None si los dos turnos son contiguos o si falta alguno.
        """
        td = self.franja("TD", momento)
        tn = self.franja("TN", momento)
        if td is None or tn is None or td[1] == tn[0]:
            return None
        return (td[1], tn[0])

    def en_hueco_termoformado(self, area: str, momento: datetime) -> bool:
        if area != AREA_CON_HUECO:
            return False
        hueco = self.hueco(momento)
        return hueco is not None and _dentro(momento.time(), *hueco)

    def turno_por_hora(self, area: str, momento: datetime) -> str | None:
        """El turno que le corresponde a un instante según el JSON, sin excepciones.

        En el solape de Extrusión gana el primero de `turnos_permitidos`, que es
        TD. Devuelve None si no hay franja que lo contenga.
        """
        for turno in self.permitidos(area):
            franja = self.franja(turno, momento)
            if franja and _dentro(momento.time(), *franja):
                return turno
        return None

    def _turno_vigente_en(self, area: str, momento: datetime) -> str | None:
        """Como `turno_por_hora()`, pero sigue un turno hasta el día siguiente.

        `turno_por_hora()` usa `franja()`, que sólo mira la franja propia de
        `momento.date()`: un turno nocturno que sigue corriendo en un día sin
        horario propio (sábado sin definición, tras el TN del viernes) le da
        `None` aunque el turno siga activo. Usa `franja_vigente()` -- la misma
        fuente de verdad que `ventana_turno()` para el fin del tramo -- para
        que el inicio y el fin de cada tramo se decidan con el mismo criterio;
        tener dos lecturas distintas de la misma ambigüedad (madrugada propia
        vs. cola de ayer) fue justamente el bug que hizo que `tramos()` dejara
        de avanzar en un viernes cuyo horario difiere del jueves.
        """
        for turno in self.permitidos(area):
            franja = self.franja_vigente(turno, momento)
            if franja and _dentro(momento.time(), *franja):
                return turno
        return None

    # -- la regla de negocio ----------------------------------------------

    def clasificar(self, linea, declarado, inicio) -> tuple[str, str]:
        """(turno, caso) de una fila. `inicio` va en hora local de planta.

        El orden importa: el hueco de Termoformado se evalúa antes que el dato
        de origen porque es la única regla que lo pisa; y la vigencia se mira
        antes que nada, porque sin ella no hay horario contra el cual comparar.
        """
        if not isinstance(inicio, datetime):
            return declarado, Caso.SIN_AREA
        area = normalizar_area(linea)
        if area is None:
            return declarado, Caso.SIN_AREA

        if self.vigencia_para(inicio) is None:
            # Antes de la vigencia más antigua no hay horario que aplicar. Se
            # deja lo que vino y la corrida lo informa: inventar un turno acá
            # es corromper el histórico en silencio.
            return declarado, Caso.SIN_VIGENCIA

        if self.en_hueco_termoformado(area, inicio):
            return TURNO_DEL_HUECO, Caso.HUECO

        if _sin_tildes(declarado) not in SIN_DATO:
            return declarado, Caso.ORIGEN

        calculado = self.turno_por_hora(area, inicio)
        if calculado is not None:
            return calculado, Caso.CALCULADO

        if inicio.weekday() >= 5:
            # El JSON no define sábado ni domingo. Se clasifican como Día y no
            # suman extras (ver `sin_horas_extra`).
            return TURNO_FIN_DE_SEMANA, Caso.FIN_DE_SEMANA

        return declarado, Caso.SIN_VIGENCIA

    def resolver(self, linea, declarado, inicio) -> str:
        return self.clasificar(linea, declarado, inicio)[0]

    def fin_de_turno(self, linea, turno: str, inicio: datetime) -> datetime | None:
        """Instante en que termina oficialmente el turno de esa fila.

        Es el fin que dice el JSON, **sin** la extensión del hueco: las horas
        extra se cuentan contra las 18:00 (o las 15:00 del viernes) aunque para
        clasificar el Día de Termoformado llegue hasta que arranca la noche.

        Usa `ventana_turno()`, que resuelve el fin aunque `inicio` ya haya
        cruzado a un día sin horario propio en el JSON (el TN del viernes,
        entrado el sábado de madrugada): sin eso, esas filas se quedaban sin
        fin de turno y por lo tanto sin minutos extra.
        """
        area = normalizar_area(linea)
        if area is None or not isinstance(inicio, datetime):
            return None
        ventana = self.ventana_turno(turno, inicio)
        return ventana[1] if ventana else None

    # -- fragmentación por turno -------------------------------------------

    def _inicio_siguiente_turno(
        self, area: str, cursor: datetime, horizonte_dias: int = 14
    ) -> datetime | None:
        """Cuándo empieza el próximo turno definido después de `cursor`.

        Mira día por día hasta `horizonte_dias` hacia adelante en vez de sólo
        el día de `cursor`: un tramo sin turno puede durar más de un día (un
        fin de semana largo, un corte de varios días), y nada distingue un día
        TNL del siguiente como para cortarlo ahí. `None` si no encuentra nada
        en el horizonte -- el llamador lo trata como "el resto queda TNL".
        """
        permitidos = self.permitidos(area)
        if not permitidos:
            return None
        for offset in range(horizonte_dias + 1):
            dia = cursor.date() + timedelta(days=offset)
            candidatos = []
            for turno in permitidos:
                franja = self.franja(turno, datetime.combine(dia, time(12, 0)))
                if franja is None:
                    continue
                inicio_turno = datetime.combine(dia, franja[0])
                if inicio_turno > cursor:
                    candidatos.append(inicio_turno)
            if candidatos:
                return min(candidatos)
        return None

    def _fin_de_tramo(self, area: str, cursor: datetime, turno: str | None) -> datetime | None:
        """Cuándo termina el tramo que arranca en `cursor`.

        Si `turno` es uno real (TD/TT/TN), es el fin de su propia ventana --
        ya resuelve el cruce de medianoche. Si es `None` (tramo no cubierto:
        hueco o fin de semana), es el inicio del próximo turno definido.
        """
        if turno is not None:
            ventana = self.ventana_turno(turno, cursor)
            if ventana is not None:
                return ventana[1]
        return self._inicio_siguiente_turno(area, cursor)

    def tramos(self, linea, inicio, fin, comentario=None) -> list[tuple[datetime, datetime, str | None, str]]:
        """Fragmenta `[inicio, fin)` en tramos de un único turno cada uno.

        Cada elemento es `(inicio_tramo, fin_tramo, turno, caso)`. `turno` es
        `TD`/`TT`/`TN` cuando el tramo cae dentro de una franja de
        `turnos_permitidos`, `TNL` cuando no (fin de semana, el hueco entre
        turnos, o feriado), o `None` cuando no hay base para decidir (área
        desconocida, o fecha anterior a toda vigencia) -- en ese caso el
        llamador debe conservar lo que trajo el origen, no inventar.

        El feriado (`es_feriado()`, que lee `comentario`) pisa cualquier turno
        que el proveedor ya haya declarado: es la misma clase de excepción que
        el hueco de Termoformado en `clasificar()`, pero por comentario en vez
        de por hora. No se fragmenta más allá de eso: dentro de un feriado no
        hay nada que distinga un tramo de otro.

        Los límites son medio-abiertos (`[inicio, fin)`), igual que el resto
        del módulo: un intervalo que empieza o termina justo en un borde de
        turno cae entero de un lado, sin generar tramos de duración cero.

        No decide por sí sola cuál vigencia aplica a fechas *futuras* a las
        que ya conoce, ni fragmenta más allá del primer punto sin vigencia:
        una fila de fecha anterior a la vigencia más antigua se devuelve
        entera (ver `Caso.SIN_VIGENCIA`), igual que hoy hace `clasificar()`.
        """
        if not isinstance(inicio, datetime) or not isinstance(fin, datetime) or fin <= inicio:
            return []

        area = normalizar_area(linea)
        if area is None:
            return [(inicio, fin, None, Caso.SIN_AREA)]

        if es_feriado(comentario):
            return [(inicio, fin, TNL, Caso.FERIADO)]

        tramos: list[tuple[datetime, datetime, str | None, str]] = []
        cursor = inicio
        while cursor < fin:
            if self.vigencia_para(cursor) is None:
                tramos.append((cursor, fin, None, Caso.SIN_VIGENCIA))
                break
            turno = self._turno_vigente_en(area, cursor)
            fin_tramo = self._fin_de_tramo(area, cursor, turno)
            if fin_tramo is None or fin_tramo > fin or fin_tramo <= cursor:
                # `fin_tramo <= cursor` no debería pasar -- significaría un
                # tramo que no avanza -- pero un horario mal formado en
                # horarios.json (o una futura vigencia con un patrón que hoy
                # no existe) podría producirlo. Cerrar acá en vez de girar en
                # el lugar: un ETL colgado es peor que un tramo final de más.
                fin_tramo = fin
            caso = Caso.CALCULADO if turno else Caso.NO_CUBIERTO
            tramos.append((cursor, fin_tramo, turno or TNL, caso))
            cursor = fin_tramo
        return tramos


def sin_horas_extra(caso: str) -> bool:
    """Casos en que no corresponde contar minutos extra.

    Fin de semana y filas sin vigencia: en los dos el turno lo pusimos nosotros
    sin un horario del config detrás, así que tampoco hay un fin de jornada
    contra el cual medir el exceso.
    """
    return caso in (Caso.FIN_DE_SEMANA, Caso.SIN_VIGENCIA)


def cargar(cfg: Mapping) -> Horarios:
    return Horarios(cfg)
