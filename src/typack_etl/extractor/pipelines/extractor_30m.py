"""
Extractor de últimos 30 días (ventana móvil) del reporte "sabana".

Reglas del rango por defecto:
  - Fecha de ejecución ("hoy") según TZ (America/Santiago por defecto)
  - end = ayer (hoy - 1 día) 23:59:59
  - start = end - 29 días (30 días corridos incluyendo end)

Modos:
  - Single-Line: si se indica --devid descarga sólo esa línea.
  - Multi-Línea: si se omite --devid descarga todas las líneas de --lineas-json.

Archivos generados:
  - CSV por línea: sabana_{start}_{end}_{name}_{devid}.csv
	(delimitador ; y decimales con ,)
  - plan_<timestamp>.csv y results_<timestamp>.csv en --logs-dir

Argumentos principales:
  --devid           Línea específica (modo single-line)
  --lineas-json     JSON con {"lineas": [{"name":..., "devid":...}]}
  --credenciales    JSON con USER y PASS
  --output          Archivo (single) o directorio (multi); si en multi apunta a .csv se usa su carpeta
  --logs-dir        Directorio para logs
  --start-date / --end-date  (Opcionales) Para sobrescribir el rango automático
  --no-progress     Oculta barra de progreso en multi-línea
	--days            Cantidad de días hacia atrás (incluyendo ayer). Default: 30
"""

from __future__ import annotations

import argparse
import calendar
import csv
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, List, Mapping, Sequence


def _ensure_src_on_path() -> None:
	here = Path(__file__).resolve()
	for parent in here.parents:
		candidate = parent / "src"
		if candidate.is_dir():
			sp = str(candidate)
			if sp not in sys.path:
				sys.path.insert(0, sp)
			break


try:  # pragma: no cover
	from typack_etl.extractor.services.auth import get_token
	from typack_etl.extractor.services.reports import get_sabana_report
except Exception:  # noqa: BLE001
	_ensure_src_on_path()
	from typack_etl.extractor.services.auth import get_token
	from typack_etl.extractor.services.reports import get_sabana_report


def _safe_filename(text: str) -> str:
	return "".join(c if c.isalnum() or c in ("-", "_", ".") else "_" for c in text)


def _format_value(value):
	if isinstance(value, float):
		return ("%s" % value).replace(".", ",")
	if isinstance(value, int):
		return str(value)
	if isinstance(value, str):
		if value.replace("-", "").replace(".", "", 1).isdigit() and "." in value:
			return value.replace(".", ",")
		return value
	return value


def _infer_rows(data) -> List[Mapping]:
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


def write_csv(rows: Sequence[Mapping], output_path: Path) -> int:
	output_path.parent.mkdir(parents=True, exist_ok=True) 
	if not rows:
		with output_path.open("w", encoding="utf-8-sig", newline="") as f:
			pass
		return 0
	fieldnames = _union_fieldnames(rows)
	with output_path.open("w", encoding="utf-8-sig", newline="") as f:
		writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore", delimiter=";")
		writer.writeheader()
		for r in rows:
			formatted = {k: _format_value(r.get(k)) for k in fieldnames}
			writer.writerow(formatted)
	return len(rows)


def _load_lineas(json_path: Path) -> list[dict]:
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
	_ensure_logs_dir(logs_dir)
	path = logs_dir / f"plan_{_timestamp()}.csv"
	with path.open("w", encoding="utf-8") as f:
		f.write("line;devid;start;end;output_path\n")
		for it in items:
			f.write(f"{it['line']};{it['devid']};{it['start']};{it['end']};{it['output_path']}\n")
	return path


def _write_results_log(items: list[dict], logs_dir: Path) -> Path:
	_ensure_logs_dir(logs_dir)
	path = logs_dir / f"results_{_timestamp()}.csv"
	with path.open("w", encoding="utf-8") as f:
		f.write("line;devid;start;end;rows;success;error\n")
		for it in items:
			f.write(f"{it['line']};{it['devid']};{it['start']};{it['end']};{it['rows']};{int(it['success'])};{it['error']}\n")
	return path


def _progress(iterable, total: int, enabled: bool = True):
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
	print()


def _auto_range(days: int) -> tuple[str, str]:
	"""Genera rango automático usando 'days' hacia atrás, terminando en ayer.

	days=30 -> último día = ayer; inicio = ayer - (30-1) días.
	"""
	if days < 1:
		days = 1
	today = datetime.now()
	end_date = today - timedelta(days=1)
	start_date = end_date - timedelta(days=days - 1)
	start_str = f"{start_date:%Y-%m-%d} 00:00"
	end_str = f"{end_date:%Y-%m-%d} 23:59:59"
	return start_str, end_str


def parse_args(argv: Iterable[str] | None = None):
	p = argparse.ArgumentParser(description="Descarga ventana móvil de días del reporte sabana.")
	p.add_argument("--start-date", required=False, help="Override inicio (default: automático últimos 30 días)")
	p.add_argument("--end-date", required=False, help="Override fin (default: automático últimos 30 días)")
	p.add_argument("--devid", required=False, help="ID de la línea/máquina (single-line)")
	p.add_argument("--lineas-json", required=False, default="config/lineas.json", help="Ruta al JSON de líneas")
	p.add_argument("--output", required=False, help="Archivo (single) o directorio (multi) destino")
	p.add_argument("--credenciales", required=False, default="config/credenciales.json", help="Credenciales USER/PASS")
	p.add_argument("--logs-dir", required=False, default="data/processed/logs", help="Directorio de logs plan/resultados")
	p.add_argument("--no-progress", action="store_true", help="Oculta barra de progreso en multi-línea")
	p.add_argument("--days", type=int, required=False, default=30, help="Cantidad de días hacia atrás (default: 30)")
	return p.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
	args = parse_args(argv)
	if not args.start_date or not args.end_date:
		auto_start, auto_end = _auto_range(args.days)
		start = args.start_date or auto_start
		end = args.end_date or auto_end
		if not args.start_date or not args.end_date:
			print(f"Usando rango automático últimos {args.days} días: start={start} end={end}")
	else:
		start = args.start_date
		end = args.end_date
	devid = args.devid
	logs_dir = Path(args.logs_dir)

	print("Obteniendo token...")
	token = get_token(args.credenciales)

	if devid:  # single-line
		if args.output:
			out_path = Path(args.output)
		else:
			base_dir = Path("data") / "processed"
			out_name = f"sabana_{_safe_filename(start)}_{_safe_filename(end)}_{_safe_filename(devid)}.csv"
			out_path = base_dir / out_name

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

	# multi-line
	lineas_path = Path(args.lineas_json)
	lineas = _load_lineas(lineas_path)
	if not lineas:
		print("No se encontraron líneas para procesar. Finalizando.")
		return 1

	if args.output:
		provided = Path(args.output)
		if provided.suffix.lower() == ".csv":
			print(f"Aviso: --output apunta a archivo .csv; se usará su carpeta padre: {provided.parent}")
			base_dir = provided.parent
		else:
			base_dir = provided
	else:
		base_dir = Path("data") / "processed"

	print(f"Procesando {len(lineas)} líneas (últimos 30 días) ...")

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