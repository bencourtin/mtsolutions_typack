"""
Landing inmutable: la respuesta cruda de la API, tal como llegó.

Por qué existe
--------------
El libro de auditoría dice *qué* cambió y *cuándo*, pero se construye
comparando el CSV ya escrito contra la respuesta nueva. Si mañana hay que
peritar una discrepancia —por ejemplo, entender por qué una línea perdió 44 h
de cobertura— hace falta poder volver a lo que la API respondió aquel día,
antes de cualquier normalización nuestra.

Eso es lo que guarda este módulo, y por eso se escribe **antes** de tocar los
datos y **nunca se sobrescribe**.

Formato
-------
Un archivo por corrida y línea: `<snapshots>/YYYY/MM/DD/{run_id}_{devid}.jsonl.gz`

  - primera línea: el sobre, con el contexto de la consulta
  - líneas siguientes: un registro crudo por línea, tal como vino

Se guarda también cuando la descarga falla: una corrida fallida es
justamente lo que interesa poder reconstruir.

El manifiesto (`manifest.jsonl`) es el índice append-only de todas las
corridas: una línea por (corrida, línea de producción), con el estado y la
ruta de su snapshot. Sobrevive a la rotación de los snapshots.
"""

from __future__ import annotations

import gzip
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from typack_etl.extractor.utils.date_utils import now_planta

MANIFEST_FILENAME = "manifest.jsonl"


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------

def ruta_snapshot(snapshots_dir: Path, run_id: str, devid, momento: datetime | None = None) -> Path:
    """Ruta del snapshot de una línea dentro de una corrida.

    Se particiona por día para que un directorio no acumule miles de archivos
    y para que la rotación pueda borrar por fecha sin abrir nada.
    """
    momento = momento or now_planta()
    return (
        Path(snapshots_dir)
        / f"{momento:%Y}" / f"{momento:%m}" / f"{momento:%d}"
        / f"{run_id}_{devid}.jsonl.gz"
    )


def escribir_snapshot(
    destino: Path,
    sobre: Mapping[str, Any],
    data: Any,
) -> Path:
    """Guarda la respuesta cruda comprimida. No sobrescribe.

    `data` se guarda tal como vino de la API: si es una lista, un registro por
    línea; si es cualquier otra cosa, una sola línea con el objeto entero. El
    sobre deja constancia de cuál de los dos casos fue.
    """
    destino = Path(destino)
    if destino.exists():
        raise FileExistsError(f"El snapshot ya existe y no se sobrescribe: {destino}")

    destino.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, list):
        registros: Sequence[Any] = data
        forma = "lista"
    elif data is None:
        registros, forma = [], "vacio"
    else:
        registros, forma = [data], "objeto"

    cabecera = {**dict(sobre), "forma": forma, "n_registros": len(registros)}

    tmp = destino.with_name(destino.name + ".tmp")
    try:
        with gzip.open(tmp, "wt", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(cabecera, ensure_ascii=False, default=str) + "\n")
            for registro in registros:
                f.write(json.dumps(registro, ensure_ascii=False, default=str) + "\n")
        os.replace(tmp, destino)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return destino


def leer_snapshot(path: Path) -> Tuple[Dict[str, Any], List[Any]]:
    """Devuelve (sobre, registros) de un snapshot."""
    with gzip.open(Path(path), "rt", encoding="utf-8") as f:
        lineas = f.read().splitlines()
    if not lineas:
        return {}, []
    return json.loads(lineas[0]), [json.loads(x) for x in lineas[1:] if x.strip()]


def rotar_snapshots(snapshots_dir: Path, dias: int) -> int:
    """Borra los snapshots de días anteriores a la retención.

    Recorre las carpetas YYYY/MM/DD y borra por fecha, sin abrir los archivos.
    El manifiesto NO se rota: es el índice que dice qué hubo, aunque el
    contenido ya no esté.
    """
    snapshots_dir = Path(snapshots_dir)
    if dias <= 0 or not snapshots_dir.exists():
        return 0

    limite = (now_planta() - timedelta(days=dias)).date()
    borrados = 0
    for dia_dir in sorted(snapshots_dir.glob("*/*/*")):
        if not dia_dir.is_dir():
            continue
        try:
            fecha = datetime.strptime("-".join(dia_dir.parts[-3:]), "%Y-%m-%d").date()
        except ValueError:
            continue  # estructura inesperada: no se toca
        if fecha >= limite:
            continue
        for archivo in dia_dir.glob("*.jsonl.gz"):
            try:
                archivo.unlink()
                borrados += 1
            except OSError:
                pass
        _borrar_si_vacio(dia_dir)
    return borrados


def _borrar_si_vacio(directorio: Path) -> None:
    """Limpia las carpetas de día, mes y año que hayan quedado vacías."""
    for _ in range(3):
        try:
            if any(directorio.iterdir()):
                return
            directorio.rmdir()
            directorio = directorio.parent
        except OSError:
            return


# ---------------------------------------------------------------------------
# Manifiesto
# ---------------------------------------------------------------------------

def append_manifest(entradas: Sequence[Mapping[str, Any]], snapshots_dir: Path) -> Path:
    """Agrega entradas al índice append-only de corridas.

    Se serializa todo a memoria y se vuelca de una vez, para que una caída no
    deje media línea a mitad del archivo.
    """
    destino = Path(snapshots_dir) / MANIFEST_FILENAME
    destino.parent.mkdir(parents=True, exist_ok=True)
    if not entradas:
        return destino

    bloque = "".join(
        json.dumps(dict(e), ensure_ascii=False, default=str) + "\n" for e in entradas
    )
    with destino.open("a", encoding="utf-8", newline="\n") as f:
        f.write(bloque)
    return destino


def leer_manifest(snapshots_dir: Path) -> List[Dict[str, Any]]:
    """Lee el manifiesto completo. Archivo inexistente -> []."""
    destino = Path(snapshots_dir) / MANIFEST_FILENAME
    if not destino.exists():
        return []
    entradas = []
    with destino.open(encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if not linea:
                continue
            try:
                entradas.append(json.loads(linea))
            except json.JSONDecodeError:
                continue  # una línea corrupta no invalida el resto del índice
    return entradas


def buscar_en_manifest(
    snapshots_dir: Path,
    linea: str | None = None,
    run_id: str | None = None,
    estado: str | None = None,
) -> List[Dict[str, Any]]:
    """Filtra el manifiesto. Sirve para responder "qué pasó con esta línea"."""
    return [
        e for e in leer_manifest(snapshots_dir)
        if (linea is None or e.get("linea") == linea)
        and (run_id is None or e.get("run_id") == run_id)
        and (estado is None or e.get("estado") == estado)
    ]
