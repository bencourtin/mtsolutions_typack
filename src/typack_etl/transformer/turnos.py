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
from datetime import date, datetime, time
from typing import Mapping

# Lo que el origen manda cuando no sabe qué turno es.
SIN_DATO = frozenset({"", "T0", "-", "0", "NONE", "NAN"})

# El turno al que van el hueco de Termoformado y el fin de semana sin horario.
TURNO_DEL_HUECO = "TD"
TURNO_FIN_DE_SEMANA = "TD"
AREA_CON_HUECO = "Termoformado"

_AREAS_DATOS = {"TERMOFORMADO": "Termoformado", "EXTRUSION": "Extrusion"}

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
        # a la fecha buscada es la que rige.
        self.vigencias = sorted(
            (Vigencia(v) for v in cfg["vigencias"] if v.get("vigente")),
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
        """
        vigencia = self.vigencia_para(momento)
        if vigencia is None or not isinstance(momento, datetime):
            return None
        return vigencia.franja(turno, DIAS[momento.weekday()])

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
        """
        from datetime import timedelta

        area = normalizar_area(linea)
        if area is None or not isinstance(inicio, datetime):
            return None
        franja = self.franja(turno, inicio)
        if franja is None:
            return None
        arranca, termina = franja
        fin = datetime.combine(inicio.date(), termina)
        if termina <= arranca and inicio.time() >= arranca:
            # Turno que cruza medianoche y el registro está en el tramo previo.
            fin += timedelta(days=1)
        return fin


def sin_horas_extra(caso: str) -> bool:
    """Casos en que no corresponde contar minutos extra.

    Fin de semana y filas sin vigencia: en los dos el turno lo pusimos nosotros
    sin un horario del config detrás, así que tampoco hay un fin de jornada
    contra el cual medir el exceso.
    """
    return caso in (Caso.FIN_DE_SEMANA, Caso.SIN_VIGENCIA)


def cargar(cfg: Mapping) -> Horarios:
    return Horarios(cfg)
