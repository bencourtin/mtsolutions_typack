"""
Script de prueba para descargar el reporte "sabana" usando los servicios
auth.get_token y reports.get_sabana_report, guardando el resultado en CSV.

Uso (recomendado):
  - Como módulo (mantiene imports limpios con layout src/):
	  python -m src.typack_etl.extractor.pipelines.test \
		  --start-date 2025-01-01 --end-date 2025-01-02 --devid 1234

  - O directamente al archivo (intenta ajustar sys.path automáticamente):
	  python src/typack_etl/extractor/pipelines/test.py \
		  --start-date 2025-01-01 --end-date 2025-01-02

Parámetros:
  --start-date  Fecha/hora inicio (cadena que acepta la API). Ej: 2025-01-01 00:00
  --end-date    Fecha/hora fin (cadena que acepta la API).    Ej: 2025-01-02 00:00
  --devid       (Opcional) Identificador de la línea/máquina para filtrar.
  --output      (Opcional) Ruta del CSV de salida. Por defecto, data/processed/...
  --credenciales (Opcional) Ruta al JSON de credenciales. Por defecto, config/credenciales.json

Salida:
  CSV con las filas retornadas por el endpoint. Si no hay datos, el archivo
  contendrá sólo cabeceras (si es posible inferirlas) o se omitirá la creación
  según el caso.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path
from typing import Iterable, List, Mapping, Sequence


def _ensure_src_on_path() -> None:
	"""Intenta agregar el directorio 'src' al sys.path si no está.

	Esto permite ejecutar el script directamente con `python src/.../test.py`
	sin tener que usar `-m`, respetando el layout de proyecto basado en `src/`.
	"""
	# Si ya podemos importar, no hacemos nada (se intentará más abajo).
	# Buscamos 'src' ascendiendo desde este archivo.
	here = Path(__file__).resolve()
	for parent in here.parents:
		candidate = parent / "src"
		if candidate.is_dir():
			sp = str(candidate)
			if sp not in sys.path:
				sys.path.insert(0, sp)
			break


# Intentar importación directa; si falla, ajustar sys.path y reintentar
try:  # pragma: no cover - simple robustez de ejecución
	from typack_etl.extractor.services.auth import get_token
	from typack_etl.extractor.services.reports import get_sabana_report
except Exception:  # noqa: BLE001
	_ensure_src_on_path()
	from typack_etl.extractor.services.auth import get_token
	from typack_etl.extractor.services.reports import get_sabana_report


def _infer_rows(data) -> List[Mapping]:
	"""Normaliza la respuesta a una lista de mapeos (dicts por fila).

	Acepta:
	  - lista de dicts -> se retorna tal cual
	  - dict con clave 'data' o 'results' que contenga lista -> retorna esa lista
	  - dict simple -> lo envuelve en una lista
	  - cualquier otro -> retorna lista vacía
	"""
	if isinstance(data, list):
		return [x for x in data if isinstance(x, dict)]
	if isinstance(data, dict):
		for key in ("data", "results", "items", "rows"):
			if key in data and isinstance(data[key], list):
				return [x for x in data[key] if isinstance(x, dict)]
		return [data]
	return []


def _union_fieldnames(rows: Sequence[Mapping]) -> List[str]:
	keys = []
	seen = set()
	for r in rows:
		for k in r.keys():
			if k not in seen:
				seen.add(k)
				keys.append(k)
	return keys


def _safe_filename(text: str) -> str:
	return "".join(c if c.isalnum() or c in ("-", "_", ".") else "_" for c in text)


def _format_value(value):
	"""Convierte valores numéricos a formato de cadena con coma decimal.

	Reglas:
	  - float -> reemplaza punto por coma
	  - int -> str normal
	  - str que representa un número decimal (ej: 12.34) -> reemplaza punto por coma
	  - otros tipos -> se devuelve tal cual
	"""
	if isinstance(value, float):
		# Usar representación estándar y reemplazar punto
		return ("%s" % value).replace(".", ",")
	if isinstance(value, int):
		return str(value)
	if isinstance(value, str):
		# Detectar patrón decimal simple
		if value.replace("-", "").replace(".", "", 1).isdigit() and "." in value:
			return value.replace(".", ",")
		return value
	return value


def write_csv(rows: Sequence[Mapping], output_path: Path) -> int:
	output_path.parent.mkdir(parents=True, exist_ok=True)
	if not rows:
		# Crear CSV vacío con cabeceras si no hay filas: no conocemos cabeceras, así que omitimos.
		# En su lugar, creamos un archivo con solo BOM + sin filas ni cabeceras para indicar "sin datos".
		with output_path.open("w", encoding="utf-8-sig", newline="") as f:
			pass
		return 0

	fieldnames = _union_fieldnames(rows)
	with output_path.open("w", encoding="utf-8-sig", newline="") as f:
		writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore", delimiter=";")
		writer.writeheader()
		for r in rows:
			# Formatear cada valor según reglas de coma decimal
			formatted = {k: _format_value(r.get(k)) for k in fieldnames}
			writer.writerow(formatted)
	return len(rows)


def parse_args(argv: Iterable[str] | None = None):
	parser = argparse.ArgumentParser(
		description="Descarga el reporte 'sabana' y lo guarda como CSV.",
	)
	parser.add_argument("--start-date", required=True, help="Fecha/hora inicio (string aceptado por la API)")
	parser.add_argument("--end-date", required=True, help="Fecha/hora fin (string aceptado por la API)")
	parser.add_argument("--devid", required=False, help="ID de la línea/máquina (opcional)")
	parser.add_argument(
		"--output",
		required=False,
		help="Ruta del CSV de salida. Por defecto data/processed/sabana_...csv",
	)
	parser.add_argument(
		"--credenciales",
		required=False,
		default="config/credenciales.json",
		help="Ruta al JSON de credenciales (default: config/credenciales.json)",
	)
	return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
	args = parse_args(argv)

	start = args.start_date
	end = args.end_date
	devid = args.devid

	# Construir ruta por defecto del CSV si no se entrega
	if args.output:
		out_path = Path(args.output)
	else:
		# data/processed/sabana_{start}_{end}[_devid].csv
		base_dir = Path("data") / "processed"
		suffix = f"_{_safe_filename(devid)}" if devid else ""
		out_name = f"sabana_{_safe_filename(start)}_{_safe_filename(end)}{suffix}.csv"
		out_path = base_dir / out_name

	print("Obteniendo token...")
	token = get_token(args.credenciales)

	print(f"Consultando sabana: start={start} end={end} devid={devid or '-'} ...")
	data = get_sabana_report(token, start, end, devid)
	rows = _infer_rows(data)

	count = write_csv(rows, out_path)
	print(f"Guardado CSV: {out_path} ({count} filas)")

	if count == 0:
		print("Advertencia: no se recibieron filas para los parámetros indicados.")
	return 0


if __name__ == "__main__":  # pragma: no cover
	try:
		raise SystemExit(main())
	except KeyboardInterrupt:
		print("Interrumpido por el usuario.")
		raise SystemExit(130)

