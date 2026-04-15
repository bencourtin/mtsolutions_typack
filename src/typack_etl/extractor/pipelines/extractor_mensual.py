"""
Parámetros:
	--start-date  Fecha/hora inicio (cadena que acepta la API). Si no se indica, se usa el 1er día del mes en curso a las 00:00.
	--end-date    Fecha/hora fin (cadena que acepta la API).    Si no se indica, se usa el último día del mes en curso a las 23:59:59.
	--devid       (Opcional) Identificador de la línea/máquina para filtrar. Si se omite se descargan todas las líneas definidas en config/lineas.json.
  --output      (Opcional) Ruta del CSV de salida. Por defecto, data/processed/...
  --logs-dir    (Opcional) Directorio donde se guardarán plan_*.csv y results_*.csv (default: data/processed/logs)
  --no-progress (Flag) Si se incluye, no muestra barra de progreso en modo multi-línea

Salida:
  CSV con las filas retornadas por el endpoint. Si no hay datos, el archivo
  contendrá sólo cabeceras (si es posible inferirlas) o se omitirá la creación
  según el caso.
"""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path
from typing import Iterable, List, Mapping, Sequence
import calendar
from datetime import datetime
import time

from typack_etl.extractor.services.auth import get_token
from typack_etl.extractor.services.reports import get_sabana_report
from config.settings import PATHS, load_lineas


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


def _current_month_range_strings() -> tuple[str, str]:
		"""Devuelve (start_str, end_str) para el mes en curso.

		Formato:
			- start: YYYY-MM-01 00:00
			- end:   YYYY-MM-<last> 23:59:59
		"""
		now = datetime.now()
		year, month = now.year, now.month
		last_day = calendar.monthrange(year, month)[1]
		start = f"{year:04d}-{month:02d}-01 00:00"
		end = f"{year:04d}-{month:02d}-{last_day:02d} 23:59:59"
		return start, end


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





def _get_month_name_spanish(month_num: int) -> str:
	"""Convierte número de mes (1-12) a nombre en español."""
	month_names = {
		1: "enero", 2: "febrero", 3: "marzo", 4: "abril",
		5: "mayo", 6: "junio", 7: "julio", 8: "agosto",
		9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre"
	}
	return month_names.get(month_num, str(month_num))


def _parse_date_components(date_str: str) -> tuple[int, int, str]:
	"""Extrae año, mes numérico y nombre de mes desde fecha YYYY-MM-DD."""
	try:
		date_part = date_str.split(" ")[0]  # Tomar solo la parte de fecha
		year, month, _ = date_part.split("-")
		year_int = int(year)
		month_int = int(month)
		month_name = _get_month_name_spanish(month_int)
		return year_int, month_int, month_name
	except Exception:
		# Fallback a fecha actual si no se puede parsear
		now = datetime.now()
		return now.year, now.month, _get_month_name_spanish(now.month)


def _timestamp() -> str:
	return datetime.now().strftime("%Y%m%d_%H%M%S")


def _ensure_logs_dir(logs_dir: Path) -> None:
	logs_dir.mkdir(parents=True, exist_ok=True)


def _write_plan_log(items: list[dict], logs_dir: Path) -> Path:
	"""Escribe plan_<ts>.csv con columnas line;devid;start;end;output_path."""
	_ensure_logs_dir(logs_dir)
	path = logs_dir / f"plan_{_timestamp()}.csv"
	with path.open("w", encoding="utf-8") as f:
		f.write("line;devid;start;end;output_path\n")
		for it in items:
			f.write(f"{it['line']};{it['devid']};{it['start']};{it['end']};{it['output_path']}\n")
	return path


def _write_results_log(items: list[dict], logs_dir: Path) -> Path:
	"""Escribe results_<ts>.csv con columnas line;devid;start;end;rows;success;error."""
	_ensure_logs_dir(logs_dir)
	path = logs_dir / f"results_{_timestamp()}.csv"
	with path.open("w", encoding="utf-8") as f:
		f.write("line;devid;start;end;rows;success;error\n")
		for it in items:
			f.write(f"{it['line']};{it['devid']};{it['start']};{it['end']};{it['rows']};{int(it['success'])};{it['error']}\n")
	return path


def _progress(iterable, total: int, enabled: bool = True):
	"""Barra de progreso simple sin dependencias externas."""
	if not enabled:
		for x in iterable:
			yield x
		return
	bar_len = 30
	for i, x in enumerate(iterable, 1):
		pct = i / total
		filled = int(bar_len * pct)
		bar = "█" * filled + "·" * (bar_len - filled)
		print(f"\r[{bar}] {i}/{total} ({pct*100:5.1f}%)", end="", flush=True)
		yield x
	print()  # salto final


def parse_args(argv: Iterable[str] | None = None):
	parser = argparse.ArgumentParser(
		description="Descarga el reporte 'sabana' y lo guarda como CSV.",
	)
	parser.add_argument(
		"--start-date",
		required=False,
		help="Fecha/hora inicio (string aceptado por la API). Default: 1er día del mes actual 00:00",
	)
	parser.add_argument(
		"--end-date",
		required=False,
		help="Fecha/hora fin (string aceptado por la API). Default: último día del mes actual 23:59:59",
	)
	parser.add_argument("--devid", required=False, help="ID de la línea/máquina (opcional)")
	parser.add_argument(
		"--output",
		required=False,
		help="Ruta del CSV de salida. Por defecto data/processed/sabana_...csv",
	)
	parser.add_argument(
		"--logs-dir",
		required=False,
		default="data/processed/logs",
		help="Directorio de logs para plan y resultados (default: data/processed/logs)",
	)
	parser.add_argument(
		"--no-progress",
		action="store_true",
		help="Desactiva la barra de progreso en modo multi-línea",
	)
	return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
	args = parse_args(argv)

	# Si no se entregan fechas, usar el rango del mes en curso
	if not args.start_date or not args.end_date:
		auto_start, auto_end = _current_month_range_strings()
		start = args.start_date or auto_start
		end = args.end_date or auto_end
		if not args.start_date or not args.end_date:
			print(f"Usando rango por defecto del mes actual: start={start} end={end}")
	else:
		start = args.start_date
		end = args.end_date
	devid = args.devid

	# Cargar configuración para obtener rutas
	raw_path = PATHS.get("raw", "data/processed")
	logs_base_path = PATHS.get("logs", "data/processed/logs")
	
	# Extraer componentes de fecha para estructura de carpetas
	year, month_num, month_name = _parse_date_components(start)

	# Configurar directorio de logs con estructura año/mes
	if args.logs_dir == "data/processed/logs":  # Solo si se usa el default
		logs_dir = Path(logs_base_path) / str(year) / f"{month_num:02d}"
	else:
		logs_dir = Path(args.logs_dir)

	print("Obteniendo token...")
	token = get_token()

	# Si se especifica un devid, flujo original (una sola línea)
	if devid:
		# Construir ruta por defecto del CSV si no se entrega
		if args.output:
			out_path = Path(args.output)
		else:
			base_dir = Path(raw_path) / str(year) / f"{month_num:02d}"
			out_name = f"produccion_{year}_{month_name}_{_safe_filename(devid)}.csv"
			out_path = base_dir / out_name

		# Plan y logs para single-line
		plan_items = [{
			"line": devid,
			"devid": devid,
			"start": start,
			"end": end,
			"output_path": str(out_path)
		}]
		plan_log = _write_plan_log(plan_items, logs_dir)
		print(f"Plan registrado: {plan_log}")

		print(f"Consultando sabana: start={start} end={end} devid={devid} ...")
		data = get_sabana_report(token, start, end, devid)
		rows = _infer_rows(data)
		count = write_csv(rows, out_path)
		print(f"Guardado CSV: {out_path} ({count} filas)")
		results_items = [{
			"line": devid,
			"devid": devid,
			"start": start,
			"end": end,
			"rows": count,
			"success": count > 0,
			"error": "" if count > 0 else "SIN_DATOS"
		}]
		results_log = _write_results_log(results_items, logs_dir)
		print(f"Resultados registrados: {results_log}")
		if count == 0:
			print("Advertencia: no se recibieron filas para los parámetros indicados.")
		return 0

	# Modo multi-línea: cargar líneas desde settings y procesar todas
	lineas = load_lineas()
	if not lineas:
		print("No se encontraron líneas para procesar. Finalizando.")
		return 1

	# Determinar directorio base para salidas múltiples
	if args.output:
		# Si output termina en .csv lo tratamos como prefijo (sin sentido), avisamos y lo usamos como directorio padre
		provided = Path(args.output)
		if provided.suffix.lower() == ".csv":
			print(f"Aviso: se indicó un archivo CSV en --output para múltiples líneas; se usará su carpeta padre: {provided.parent}")
			base_dir = provided.parent
		else:
			base_dir = provided
	else:
		base_dir = Path(raw_path) / str(year) / f"{month_num:02d}"

	print(f"Procesando {len(lineas)} líneas definidas en config/lineas.json ...")

	# Construir plan previo
	plan_items = []
	for linea in lineas:
		ldev = linea.get("devid")
		lname = linea.get("name", ldev)
		out_name = f"produccion_{year}_{month_name}_{_safe_filename(lname)}.csv"
		out_path = base_dir / out_name
		plan_items.append({
			"line": lname,
			"devid": ldev,
			"start": start,
			"end": end,
			"output_path": str(out_path)
		})
	plan_log = _write_plan_log(plan_items, logs_dir)
	print(f"Plan registrado: {plan_log}")

	results_items = []
	global_count = 0
	for linea in _progress(lineas, len(lineas), enabled=not args.no_progress):
		ldev = linea.get("devid")
		lname = linea.get("name", ldev)
		try:
			data = get_sabana_report(token, start, end, ldev)
			rows = _infer_rows(data)
			out_name = f"produccion_{year}_{month_name}_{_safe_filename(lname)}.csv"
			out_path = base_dir / out_name
			count = write_csv(rows, out_path)
			global_count += count
			results_items.append({
				"line": lname,
				"devid": ldev,
				"start": start,
				"end": end,
				"rows": count,
				"success": count > 0,
				"error": "" if count > 0 else "SIN_DATOS"
			})
		except Exception as e:  # noqa: BLE001
			results_items.append({
				"line": lname,
				"devid": ldev,
				"start": start,
				"end": end,
				"rows": 0,
				"success": False,
				"error": str(e).replace(";", ",")
			})

	results_log = _write_results_log(results_items, logs_dir)
	print(f"Resultados registrados: {results_log}")
	print(f"Finalizado. Total filas acumuladas: {global_count}")
	faltan = [r for r in results_items if not r["success"]]
	if faltan:
		print("Lineas con errores o sin datos:")
		for r in faltan:
			print(f" - {r['line']} (devid={r['devid']}): {r['error'] or 'SIN_DATOS'}")
	return 0


if __name__ == "__main__":  # pragma: no cover
	try:
		raise SystemExit(main())
	except KeyboardInterrupt:
		print("Interrumpido por el usuario.")
		raise SystemExit(130)
	
