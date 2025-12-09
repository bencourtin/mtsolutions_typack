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
	--start-date  Fecha/hora inicio (cadena que acepta la API). Si no se indica, se usa el 1er día del mes en curso a las 00:00.
	--end-date    Fecha/hora fin (cadena que acepta la API).    Si no se indica, se usa el último día del mes en curso a las 23:59:59.
	--devid       (Opcional) Identificador de la línea/máquina para filtrar. Si se omite se descargan todas las líneas definidas en lineas.json.
  --output      (Opcional) Ruta del CSV de salida. Por defecto, data/processed/...
  --credenciales (Opcional) Ruta al JSON de credenciales. Por defecto, config/credenciales.json
	--lineas-json (Opcional) Ruta al JSON con las líneas (name,devid). Default: config/lineas.json
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
import sys
from pathlib import Path
from typing import Iterable, List, Mapping, Sequence
import calendar
from datetime import datetime
import time
import json


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


def _load_lineas(json_path: Path) -> list[dict]:
	"""Carga lista de lineas desde JSON con estructura {"lineas": [{"name":..., "devid":...}, ...]}.

	Retorna lista de dicts con claves 'name' y 'devid'. Si hay error, retorna lista vacía.
	"""
	try:
		with json_path.open("r", encoding="utf-8") as f:
			data = json.load(f)
		items = data.get("lineas", [])
		out = []
		for it in items:
			if isinstance(it, dict) and "devid" in it:
				out.append({"name": str(it.get("name", it["devid"])), "devid": it["devid"]})
		return out
	except FileNotFoundError:
		print(f"Aviso: no se encontró lineas.json en {json_path}.")
	except Exception as e:  # noqa: BLE001
		print(f"Error leyendo lineas.json: {e}")
	return []


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
		"--lineas-json",
		required=False,
		default="config/lineas.json",
		help="Ruta al JSON que contiene las líneas (default: config/lineas.json)",
	)
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

	logs_dir = Path(args.logs_dir)

	print("Obteniendo token...")
	token = get_token(args.credenciales)

	# Si se especifica un devid, flujo original (una sola línea)
	if devid:
		# Construir ruta por defecto del CSV si no se entrega
		if args.output:
			out_path = Path(args.output)
		else:
			base_dir = Path("data") / "processed"
			out_name = f"sabana_{_safe_filename(start)}_{_safe_filename(end)}_{_safe_filename(devid)}.csv"
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

	# Modo multi-línea: cargar lineas.json y procesar todas
	lineas_path = Path(args.lineas_json)
	lineas = _load_lineas(lineas_path)
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
		base_dir = Path("data") / "processed"

	print(f"Procesando {len(lineas)} líneas definidas en {lineas_path} ...")

	# Construir plan previo
	plan_items = []
	for linea in lineas:
		ldev = linea.get("devid")
		lname = linea.get("name", ldev)
		out_name = f"sabana_{_safe_filename(start)}_{_safe_filename(end)}_{_safe_filename(lname)}_{_safe_filename(str(ldev))}.csv"
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
			out_name = f"sabana_{_safe_filename(start)}_{_safe_filename(end)}_{_safe_filename(lname)}_{_safe_filename(str(ldev))}.csv"
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
	
	