"""Comprueba que los timestamps y los turnos digan lo mismo.

La API entrega dos cosas redundantes por cada segmento: el timestamp, que
corre en el reloj del servidor del proveedor (hora chilena con las reglas de
DST que el país dejó en 2018), y el campo `turn`, que el proveedor asigna con
la hora local real de la planta. Esa redundancia es la única forma que tenemos
de verificar el modelo temporal desde fuera, porque ningún campo dice en qué
reloj viene.

Dos usos:

  --raw     Sobre el raw, aplicando `a_local()`. Si el proveedor mueve su
            reloj otra vez, la coincidencia se desploma y el ETL avisa en vez
            de publicar un mes corrido. Es el detector que faltaba: el desfase
            era una constante en la configuración y nada lo contrastaba contra
            los datos, así que las dos ventanas de cada año pasaban inadvertidas
            desde 2024.

  (default) Sobre el consolidado ya transformado, que debe estar en hora local.
            Es el criterio de aceptación del reproceso histórico.

Un desfase de una hora no rompe nada visiblemente: mueve registros de turno,
de día operativo y de fin de semana. Por eso se mide, y por eso el corte es
duro (`--umbral`).

El "esperado" sale del mismo modelo de `turnos.Horarios` que usa el
transformer, no de una franja genérica 08-18-22: esa franja no conocía el
hueco de Termoformado, el TN adelantado del viernes ni que en Extrusión el TT
llega hasta las 23:00, así que confundía esas reglas de negocio con un reloj
corrido (falsos positivos concentrados exactamente en esas horas de borde,
verificado sobre datos reales: 545 discrepancias, el 100% en 18:00/20:00-22:00).
Fin de semana y fechas anteriores a la vigencia más antigua se excluyen de la
comparación: `horarios.json` no define horario ahí, así que no hay un
"esperado" con el que contrastar y no son evidencia de nada.
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from config.settings import PATHS, load_horarios
from typack_etl.extractor.utils.date_utils import a_local, parse_api_datetime, relojes_desfasados
from typack_etl.transformer.turnos import Caso, Horarios

# Casos de `Horarios.clasificar()` en los que no hay horario que aplicar: el
# turno "esperado" sería inventado, no derivado del JSON. Se excluyen de la
# comparación en vez de contarse como discrepancia.
SIN_ESPERADO_COMPARABLE = (Caso.SIN_AREA, Caso.SIN_VIGENCIA, Caso.FIN_DE_SEMANA)

# Cualquier valor de `turnos.SIN_DATO` sirve para forzar que `clasificar()`
# calcule el turno a partir de la hora (vigencia + hueco + franjas del día) en
# vez de devolver un eco de lo declarado, que es lo que pasaría si se le
# pasara un turno real ("TD"/"TT"/"TN").
_DECLARADO_SIN_DATO = "T0"

# El proveedor cierra el turno cuando el operador lo cierra, no al minuto, así
# que un puñado de segmentos legítimamente cae del otro lado del borde. Lo que
# delata un reloj corrido es una caída masiva, no unas pocas filas.
UMBRAL_DEFECTO = 99.5


def regimen(momento: datetime) -> str:
    """En qué régimen cae el instante: relojes alineados o proveedor +1 h.

    Se agrupa por esto y no por estación porque es la partición que importa:
    fuera de las dos ventanas de desfase los dos relojes son el mismo.
    """
    return "proveedor +1h" if relojes_desfasados(momento) else "alineados"


def turno_esperado(horarios: Horarios, linea: str, momento: datetime) -> str | None:
    """Turno que el modelo de negocio real predice para `momento`, o None.

    Usa `Horarios.clasificar()` —vigencia, hueco de Termoformado, franjas por
    día— pasando un turno "declarado" que el modelo sabe que no sabe, para que
    nunca devuelva un eco del argumento sino la respuesta calculada a partir de
    la hora. `None` cuando esa respuesta no está fundada en un horario (área
    desconocida, fin de semana, fecha anterior a la vigencia más antigua): ahí
    no hay nada que contrastar contra el reloj.
    """
    turno, caso = horarios.clasificar(linea, _DECLARADO_SIN_DATO, momento)
    if caso in SIN_ESPERADO_COMPARABLE:
        return None
    return turno


def _filas_raw(base: Path):
    """(hora local, línea, turno declarado) de cada fila del raw."""
    for archivo in sorted(base.glob("*/*/*.csv")):
        with archivo.open(encoding="utf-8-sig", newline="") as fh:
            for fila in csv.DictReader(fh, delimiter=";"):
                momento = parse_api_datetime(fila.get("start"))
                if momento is not None:
                    yield (
                        a_local(momento),
                        (fila.get("line") or "").strip(),
                        (fila.get("turn") or "").strip(),
                        archivo.name,
                    )


def _filas_consolidado(ruta: Path):
    """Ídem sobre el consolidado, que ya viene en hora local."""
    with ruta.open(encoding="utf-8-sig", newline="") as fh:
        for fila in csv.DictReader(fh, delimiter=";"):
            texto = (fila.get("Fecha_Hora_Inicio") or "").strip()
            for fmt in ("%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M:%S", "%d/%m/%Y %H:%M:%S"):
                try:
                    momento = datetime.strptime(texto, fmt)
                except ValueError:
                    continue
                yield momento, (fila.get("Linea") or "").strip(), (fila.get("Turno") or "").strip(), ruta.name
                break


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Verifica el modelo temporal contra el campo `turn` de la API.")
    p.add_argument("--raw", action="store_true", help="Verifica el raw (reloj del proveedor) en vez del consolidado")
    p.add_argument("--ruta", help="Directorio raw o archivo consolidado alternativo")
    p.add_argument("--umbral", type=float, default=UMBRAL_DEFECTO, help=f"%% mínimo de coincidencia (default: {UMBRAL_DEFECTO})")
    p.add_argument("--detalle", type=int, default=10, help="Cuántas discrepancias listar (default: 10)")
    args = p.parse_args(argv)

    if args.raw:
        base = Path(args.ruta) if args.ruta else Path(PATHS["raw"])
        origen, filas = f"raw {base}", _filas_raw(base)
    else:
        ruta = Path(args.ruta) if args.ruta else Path(PATHS["consolidated"]) / "mt_typ_consolidated.csv"
        origen, filas = f"consolidado {ruta}", _filas_consolidado(ruta)

    print(f"Verificando {origen}\n")

    horarios = Horarios(load_horarios())
    ok = defaultdict(int)
    total = defaultdict(int)
    ok_linea = defaultdict(int)
    total_linea = defaultdict(int)
    sin_esperado = 0
    discrepancias = []
    for momento, linea, declarado, fuente in filas:
        if declarado not in ("TD", "TT", "TN"):
            continue
        esperado = turno_esperado(horarios, linea, momento)
        if esperado is None:
            # Fin de semana, fecha anterior a la vigencia más antigua, o línea
            # que no mapea a un área conocida: `horarios.json` no define nada
            # ahí, así que no hay un esperado con el que contrastar.
            sin_esperado += 1
            continue
        periodo = f"{momento.year} {regimen(momento)}"
        total[periodo] += 1
        total_linea[linea] += 1
        coincide = esperado == declarado
        if coincide:
            ok[periodo] += 1
            ok_linea[linea] += 1
        elif len(discrepancias) < args.detalle:
            discrepancias.append(f"  {momento}  linea={linea}  declarado={declarado}  esperado={esperado}  ({fuente})")

    if not total:
        print("No se encontraron filas comparables (con turno declarado y horario definido). ¿Ruta equivocada?")
        return 1

    # El corte duro se decide por período (año x régimen de DST): es la
    # partición que importa para la hipótesis "el reloj se corrió", porque un
    # reloj corrido afecta parejo a todas las líneas de esa ventana de fechas.
    # La tabla por línea es sólo diagnóstico, para no confundir una ambigüedad
    # de horario de un área (concentrada en una línea, cualquier fecha) con un
    # reloj corrido (concentrado en un período, cualquier línea).
    print(f"{'período':<20}{'coincide':>12}{'filas':>10}{'%':>9}")
    fallidos = []
    for periodo in sorted(total):
        pct = 100 * ok[periodo] / total[periodo]
        marca = "" if pct >= args.umbral else "   <-- BAJO EL UMBRAL"
        print(f"{periodo:<20}{ok[periodo]:>12}{total[periodo]:>10}{pct:>8.2f}%{marca}")
        if pct < args.umbral:
            fallidos.append(periodo)

    global_pct = 100 * sum(ok.values()) / sum(total.values())
    print(f"{'TOTAL':<20}{sum(ok.values()):>12}{sum(total.values()):>10}{global_pct:>8.2f}%")
    if sin_esperado:
        print(
            f"({sin_esperado} filas excluidas: fin de semana, sin vigencia aplicable, "
            "o línea sin área conocida — horarios.json no define nada ahí)"
        )

    print(f"\n{'línea (diagnóstico)':<20}{'coincide':>12}{'filas':>10}{'%':>9}")
    for linea in sorted(total_linea):
        pct = 100 * ok_linea[linea] / total_linea[linea]
        print(f"{linea:<20}{ok_linea[linea]:>12}{total_linea[linea]:>10}{pct:>8.2f}%")

    if discrepancias:
        print("\nPrimeras discrepancias:")
        print("\n".join(discrepancias))

    if fallidos:
        print(
            f"\nFALLA: {', '.join(fallidos)} por debajo del {args.umbral}%.\n"
            "Un período entero corrido ~1 h significa que el reloj del proveedor cambió\n"
            "de reglas o que la conversión a hora local no se está aplicando. Revisar\n"
            "`date_utils.api_en_verano` y `a_local` antes de publicar.\n"
            "Si en cambio la caída se concentra en una línea (ver la tabla de arriba) y\n"
            "no en un período completo, no es el reloj: es una ambigüedad de horario de\n"
            "esa área en `horarios.json` (el mismo fenómeno que el solape TD/TT, pero en\n"
            "otro borde), y ese umbral no debería ser el que se usa para decidir si\n"
            "publicar."
        )
        return 1

    print("\nOK: el turno declarado y la hora local concuerdan.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
