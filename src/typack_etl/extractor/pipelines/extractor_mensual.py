"""
Extractor del reporte "sabana" por mes calendario. SÓLO RECARGAS HISTÓRICAS.

Este pipeline ya no forma parte del ETL diario: su ventana es el mes
calendario, por lo que no captura las reasignaciones de detenciones que
ocurren después del cierre de mes (una detención del día 28 reclasificada el 5
del mes siguiente nunca se vuelve a consultar). El ETL diario usa
`extractor_30m.py`, con ventana móvil.

Úselo para recargar un mes completo de forma puntual, indicando siempre
--start-date y --end-date. Reescribe el archivo del mes completo, sin
conservar lo previo: no lo ejecute sobre un mes que la ventana móvil esté
refrescando en paralelo.

Parámetros:
	--start-date  Fecha/hora inicio (cadena que acepta la API). Si no se indica, se usa el 1er día del mes en curso a las 00:00.
	--end-date    Fecha/hora fin (cadena que acepta la API).    Si no se indica, se usa el último día del mes en curso a las 23:59:59.
	--devid       (Opcional) Identificador de la línea/máquina para filtrar. Si se omite se descargan todas las líneas definidas en config/lineas.json.
	--output      (Opcional) Ruta del CSV de salida. Por defecto, <PATH_RAW>/<año>/<mes>/
	--logs-dir    (Opcional) Directorio donde se guardarán plan_*.csv y results_*.csv (default: <PATH_LOGS>/<año>/<mes>)
	--no-progress (Flag) Si se incluye, no muestra barra de progreso en modo multi-línea

Salida:
	CSV con las filas retornadas por el endpoint, con el esquema canónico de
	`functions.io.SABANA_COLUMNS`. Si no hay datos, el archivo se genera
	igualmente con la cabecera.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from typack_etl.extractor.functions.io import (
	infer_rows,
	progress,
	run_stamp,
	safe_filename,
	write_plan_log,
	write_results_log,
	write_window_csv,
)
from typack_etl.extractor.services.auth import get_token
from typack_etl.extractor.services.reports import get_sabana_report
from typack_etl.extractor.utils.date_utils import (
	a_planta,
	a_utc,
	fmt_api,
	inicio_mes_operativo,
	mes_operativo,
	month_name_es,
	now_planta,
	parse_api_datetime,
)
from config.settings import PATHS, load_lineas


def _current_month_range_strings() -> tuple[str, str]:
	"""Devuelve (start_str, end_str) en UTC para el mes operativo en curso.

	El mes operativo va del día 1 en el corte al día 1 del mes siguiente en el
	corte, no de medianoche a medianoche. Cada borde usa el corte de su propia
	fecha: un mes que contiene un cambio de hora no empieza y termina a la
	misma hora UTC.
	"""
	year, month = mes_operativo(now_planta())
	siguiente = (year + 1, 1) if month == 12 else (year, month + 1)
	start = a_utc(inicio_mes_operativo(year, month))
	end = a_utc(inicio_mes_operativo(*siguiente))
	return fmt_api(start), fmt_api(end)


def _parse_date_components(date_str: str) -> tuple[int, int, str]:
	"""Extrae año, mes y nombre de mes operativo desde el inicio de la ventana."""
	parsed = parse_api_datetime(date_str)
	planta = a_planta(parsed) if parsed is not None else now_planta()
	year, month = mes_operativo(planta)
	return year, month, month_name_es(month)


def _window_bounds(start: str, end: str) -> tuple[datetime, datetime]:
	"""Ventana en el reloj de planta, para reemplazar sólo el tramo declarado.

	`start` y `end` son las cadenas que recibe la API, o sea UTC; se convierten
	al espacio de los timestamps que la API devuelve.
	"""
	start_utc = parse_api_datetime(start)
	end_utc = parse_api_datetime(end)
	if start_utc is None or end_utc is None:
		raise SystemExit(
			f"Rango no interpretable (start={start!r}, end={end!r}); use 'YYYY-MM-DD HH:MM:SS'."
		)
	if start_utc > end_utc:
		raise SystemExit(f"Rango inválido: start={start} es posterior a end={end}")
	start_dt, end_dt = a_planta(start_utc), a_planta(end_utc)
	if mes_operativo(start_dt) != mes_operativo(end_dt - timedelta(seconds=1)):
		print(
			"Aviso: el rango cruza meses operativos. Este pipeline escribe una sola partición "
			f"({'-'.join(str(x) for x in mes_operativo(start_dt))}); para ventanas que cruzan meses use extractor_30m."
		)
	return start_dt, end_dt


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
		help="Ruta del CSV de salida. Por defecto <PATH_RAW>/<año>/<mes>/",
	)
	parser.add_argument(
		"--logs-dir",
		required=False,
		help="Directorio de logs para plan y resultados (default: <PATH_LOGS>/<año>/<mes>)",
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
		print(f"Usando rango por defecto del mes actual: start={start} end={end}")
	else:
		start = args.start_date
		end = args.end_date
	devid = args.devid
	window_start, window_end = _window_bounds(start, end)

	# Cargar configuración para obtener rutas
	raw_path = PATHS.get("raw", "data/raw")
	logs_base_path = PATHS.get("logs", "logs")

	# Extraer componentes de fecha para estructura de carpetas
	year, month_num, month_name = _parse_date_components(start)

	# Configurar directorio de logs con estructura año/mes
	if args.logs_dir:
		logs_dir = Path(args.logs_dir)
	else:
		logs_dir = Path(logs_base_path) / str(year) / f"{month_num:02d}"

	stamp = run_stamp()

	print("Obteniendo token...")
	token = get_token()

	# Si se especifica un devid, flujo original (una sola línea)
	if devid:
		# Construir ruta por defecto del CSV si no se entrega
		if args.output:
			out_path = Path(args.output)
		else:
			base_dir = Path(raw_path) / str(year) / f"{month_num:02d}"
			out_name = f"produccion_{year}_{month_name}_{safe_filename(devid)}.csv"
			out_path = base_dir / out_name

		# Plan y logs para single-line
		plan_items = [{
			"line": devid,
			"devid": devid,
			"start": start,
			"end": end,
			"output_path": str(out_path)
		}]
		plan_log = write_plan_log(plan_items, logs_dir, stamp)
		print(f"Plan registrado: {plan_log}")

		print(f"Consultando sabana: start={start} end={end} devid={devid} ...")
		data = get_sabana_report(token, start, end, devid)
		rows = infer_rows(data)
		stats = write_window_csv(rows, out_path, window_start, window_end)
		count = stats["nuevas"]
		print(f"Guardado CSV: {out_path} ({count} filas nuevas, {stats['conservadas']} conservadas fuera del rango)")
		results_items = [{
			"line": devid,
			"devid": devid,
			"start": start,
			"end": end,
			"rows": count,
			"success": count > 0,
			"error": "" if count > 0 else "SIN_DATOS",
			"detalle": f"{year}-{month_num:02d}:{count}(+{stats['conservadas']} conservadas)",
		}]
		results_log = write_results_log(results_items, logs_dir, stamp)
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

	def _out_path(line_name: str) -> Path:
		return base_dir / f"produccion_{year}_{month_name}_{safe_filename(line_name)}.csv"

	# Construir plan previo
	plan_items = []
	for linea in lineas:
		ldev = linea.get("devid")
		lname = linea.get("name", ldev)
		plan_items.append({
			"line": lname,
			"devid": ldev,
			"start": start,
			"end": end,
			"output_path": str(_out_path(lname))
		})
	plan_log = write_plan_log(plan_items, logs_dir, stamp)
	print(f"Plan registrado: {plan_log}")

	results_items = []
	global_count = 0
	for linea in progress(lineas, len(lineas), enabled=not args.no_progress):
		ldev = linea.get("devid")
		lname = linea.get("name", ldev)
		try:
			data = get_sabana_report(token, start, end, ldev)
			rows = infer_rows(data)
			stats = write_window_csv(rows, _out_path(lname), window_start, window_end)
			count = stats["nuevas"]
			global_count += count
			results_items.append({
				"line": lname,
				"devid": ldev,
				"start": start,
				"end": end,
				"rows": count,
				"success": count > 0,
				"error": "" if count > 0 else "SIN_DATOS",
				"detalle": f"{year}-{month_num:02d}:{count}(+{stats['conservadas']} conservadas)",
			})
		except Exception as e:  # noqa: BLE001
			results_items.append({
				"line": lname,
				"devid": ldev,
				"start": start,
				"end": end,
				"rows": 0,
				"success": False,
				"error": str(e)
			})

	results_log = write_results_log(results_items, logs_dir, stamp)
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
