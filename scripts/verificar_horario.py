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
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from config.settings import PATHS
from typack_etl.extractor.utils.date_utils import a_local, parse_api_datetime, relojes_desfasados

# Cambio de turno en hora local, de config/horarios.json.
LIMITES = ((8, "TD"), (18, "TT"), (22, "TN"))

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


def turno_por_hora(momento: datetime) -> str:
    """Turno que le corresponde a un instante de hora local."""
    hora = momento.hour + momento.minute / 60
    if LIMITES[0][0] <= hora < LIMITES[1][0]:
        return "TD"
    if LIMITES[1][0] <= hora < LIMITES[2][0]:
        return "TT"
    return "TN"


def _filas_raw(base: Path):
    """(hora local, turno declarado) de cada fila del raw."""
    for archivo in sorted(base.glob("*/*/*.csv")):
        with archivo.open(encoding="utf-8-sig", newline="") as fh:
            for fila in csv.DictReader(fh, delimiter=";"):
                momento = parse_api_datetime(fila.get("start"))
                if momento is not None:
                    yield a_local(momento), (fila.get("turn") or "").strip(), archivo.name


def _filas_consolidado(ruta: Path):
    """Ídem sobre el consolidado, que ya viene en hora local."""
    with ruta.open(encoding="utf-8-sig", newline="") as fh:
        for fila in csv.DictReader(fh, delimiter=";"):
            texto = (fila.get("Fecha_Hora_Inicio") or "").strip()
            for fmt in ("%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M:%S", "%d/%m/%Y %H:%M:%S"):
                try:
                    yield datetime.strptime(texto, fmt), (fila.get("Turno") or "").strip(), ruta.name
                    break
                except ValueError:
                    continue


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

    ok = defaultdict(int)
    total = defaultdict(int)
    discrepancias = []
    for momento, declarado, fuente in filas:
        if declarado not in ("TD", "TT", "TN"):
            continue
        periodo = f"{momento.year} {regimen(momento)}"
        total[periodo] += 1
        esperado = turno_por_hora(momento)
        if esperado == declarado:
            ok[periodo] += 1
        elif len(discrepancias) < args.detalle:
            discrepancias.append(f"  {momento}  declarado={declarado}  por hora={esperado}  ({fuente})")

    if not total:
        print("No se encontraron filas con turno declarado. ¿Ruta equivocada?")
        return 1

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

    if discrepancias:
        print("\nPrimeras discrepancias:")
        print("\n".join(discrepancias))

    if fallidos:
        print(
            f"\nFALLA: {', '.join(fallidos)} por debajo del {args.umbral}%.\n"
            "Un período entero corrido ~1 h significa que el reloj del proveedor cambió\n"
            "de reglas o que la conversión a hora local no se está aplicando. Revisar\n"
            "`date_utils.api_en_verano` y `a_local` antes de publicar."
        )
        return 1

    print("\nOK: el turno declarado y la hora local concuerdan.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
