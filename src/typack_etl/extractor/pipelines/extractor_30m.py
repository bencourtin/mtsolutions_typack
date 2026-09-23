"""
Extractor de ventana móvil del reporte "sabana". Pipeline del ETL diario.

Es el pipeline correcto para capturar reasignaciones de detenciones: su
ventana no se corta el día 1 de cada mes, de modo que una detención
reclasificada días después del evento se vuelve a consultar mientras siga
dentro de la ventana. Para recargar meses cerrados está
`scripts/recarga_historica.py`, que maneja este mismo pipeline.

Reglas del rango por defecto (día operativo, no día calendario):
  - corte = las 08:00 locales (el cambio de turno TD/TN) del día del que se
            habla, llevadas a UTC: 11:00 en verano y 12:00 en invierno. Se
            calcula por fecha, no se configura
  - end   = el último corte ya cumplido, de modo que la ventana nunca incluye
            un turno a medio andar
  - start = el corte de `days` días operativos antes

Los timestamps que recibe la API son UTC; los que devuelve están en el reloj de
su servidor, que es hora chilena con las reglas de DST anteriores a 2019 y por
eso no siempre coincide con la hora local de planta: ver `utils.date_utils`.
--start-date y --end-date se interpretan en UTC, igual que los recibe la API.

Escritura por partición
-----------------------
Los archivos se particionan por el mes de *cada registro*, no por la fecha de
la corrida, de modo que una ventana que cruza fin de mes escribe en dos
carpetas. Dentro de cada partición sólo se reemplaza el tramo que la ventana
cubre: lo anterior del mismo mes se conserva intacto. Así la corrida diaria
refresca los últimos N días sin destruir el histórico del mes.

Modos:
  - Single-Line: si se indica --devid descarga sólo esa línea.
  - Multi-Línea: si se omite --devid descarga todas las líneas de config/lineas.json.

Archivos generados:
  - CSV por línea y mes: <PATH_RAW>/{año}/{mes}/produccion_{año}_{mes_nombre}_{linea}.csv
	(delimitador ; y decimales con ,)
  - plan_<timestamp>.csv y results_<timestamp>.csv en --logs-dir

Argumentos principales:
  --devid           Línea específica (modo single-line)
  --output          Directorio base alternativo (reemplaza a PATH_RAW)
  --logs-dir        Directorio para logs
  --start-date / --end-date  (Opcionales) Para sobrescribir el rango automático
  --no-progress     Oculta barra de progreso en multi-línea
  --days            Cantidad de días hacia atrás (incluyendo ayer)
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import requests

from typack_etl.audit import cdc, reloj, traza
from typack_etl.audit.ledger import (
	append_cambios,
	claves_ausentes,
	cobertura_por_dia,
	guardar_ultimas_corridas,
	leer_ultimas_corridas,
	registrar_cobertura,
)
from typack_etl.extractor.functions.landing import (
	append_manifest,
	escribir_snapshot,
	rotar_snapshots,
	ruta_snapshot,
)
from typack_etl.extractor.functions.io import (
	infer_rows,
	month_partitions,
	nuevo_run_id,
	partition_by_month,
	progress,
	read_csv_rows,
	rotar_logs,
	run_stamp,
	safe_filename,
	write_plan_log,
	write_results_log,
	write_window_csv,
)
from typack_etl.extractor.services.auth import get_token
from typack_etl.extractor.services.reports import TokenExpirado, get_sabana_report
from typack_etl.extractor.utils.date_utils import (
	a_local,
	a_planta,
	a_utc,
	corte_utc,
	fmt_api,
	inicio_dia_operativo,
	minutos_dia_operativo,
	month_name_es,
	now_planta,
	now_utc,
	parse_api_datetime,
	utc_a_local,
)
from config.settings import (
	EXTRACTOR_WINDOW_DAYS,
	PATHS,
	RETENCION_LOGS_DIAS,
	RETENCION_SNAPSHOTS_DIAS,
	load_lineas,
)


def _auto_range(days: int) -> tuple[datetime, datetime]:
	"""Ventana móvil de `days` días operativos, en UTC, alineada al corte.

	Un día operativo va de corte a corte: las 08:00 locales, el cambio de turno
	TD/TN, que en UTC son las 11:00 en verano y las 12:00 en invierno. La
	ventana termina en el último corte ya cumplido, de modo que nunca incluye
	un turno a medio andar.

	Los dos bordes se calculan desde el calendario local de su propio día en
	vez de restar 24 h por día: en un cambio de hora el corte se mueve, y una
	ventana medida en horas fijas se saldría del turno en uno de los extremos.
	Por eso `days` cuenta días operativos y la duración en horas puede ser una
	más o una menos si la ventana cruza un cambio de hora.
	"""
	if days < 1:
		days = 1
	ahora = now_utc()
	hoy = utc_a_local(ahora).date()
	ultimo = hoy if ahora >= a_utc(inicio_dia_operativo(hoy)) else hoy - timedelta(days=1)
	end = a_utc(inicio_dia_operativo(ultimo))
	start = a_utc(inicio_dia_operativo(ultimo - timedelta(days=days)))
	return start, end


def _resolve_window(args) -> tuple[datetime, datetime, str, str]:
	"""Determina la ventana en ambos espacios de tiempo.

	Retorna (inicio_planta, fin_planta, start_str, end_str). Las cadenas van a
	la API y por lo tanto están en UTC; los datetimes están en el reloj de
	planta, que es el espacio de los timestamps que la API devuelve y con el
	que se compara, particiona y audita.

	Los overrides manuales se interpretan en UTC, igual que los recibe la API.
	"""
	auto_start, auto_end = _auto_range(args.days)

	if args.start_date:
		start_utc = parse_api_datetime(args.start_date)
		if start_utc is None:
			raise SystemExit(f"--start-date no interpretable: {args.start_date!r} (use 'YYYY-MM-DD HH:MM:SS')")
	else:
		start_utc = auto_start

	if args.end_date:
		end_utc = parse_api_datetime(args.end_date)
		if end_utc is None:
			raise SystemExit(f"--end-date no interpretable: {args.end_date!r} (use 'YYYY-MM-DD HH:MM:SS')")
	else:
		end_utc = auto_end

	start_str, end_str = fmt_api(start_utc), fmt_api(end_utc)
	if start_utc > end_utc:
		raise SystemExit(f"Rango inválido: start={start_str} es posterior a end={end_str}")

	return a_planta(start_utc), a_planta(end_utc), start_str, end_str


# Cantidades de filas que delatan una respuesta cortada por un límite del
# servidor en vez de por los datos. No es prueba, pero merece revisarse.
LIMITES_SOSPECHOSOS = (500, 1000, 2000, 5000, 10000)


class Sesion:
	"""Mantiene el token vigente y reintenta la descarga de una línea.

	Un lote de 11 líneas puede cruzar la expiración del token a mitad de
	camino: antes eso perdía todas las líneas restantes. Ahora se renueva y se
	sigue. Los fallos de red se reintentan con espera creciente, de modo que un
	corte momentáneo no deja una línea sin actualizar por 24 horas.
	"""

	def __init__(self, intentos: int = 3, espera: float = 2.0):
		self.token: str | None = None
		self.intentos = intentos
		self.espera = espera
		self.renovaciones = 0
		self.reintentos = 0

	def token_vigente(self) -> str:
		if self.token is None:
			self.token = get_token()
		return self.token

	def renovar(self) -> str:
		self.token = get_token()
		self.renovaciones += 1
		return self.token

	def sabana(self, start: str, end: str, devid):
		ultimo_error: Exception | None = None
		for intento in range(1, self.intentos + 1):
			try:
				return get_sabana_report(self.token_vigente(), start, end, devid)
			except TokenExpirado as e:
				ultimo_error = e
				self.renovar()
			except requests.RequestException as e:
				ultimo_error = e
			if intento < self.intentos:
				self.reintentos += 1
				time.sleep(self.espera * intento)
		raise ultimo_error if ultimo_error else RuntimeError("descarga fallida")


def _sobre(run_id, detectado_en, linea, ldev, start, end, http_status=None, error="") -> dict:
	"""Contexto de la consulta que acompaña a la respuesta cruda."""
	return {
		"run_id": run_id,
		"extracted_at": detectado_en.strftime("%Y-%m-%d %H:%M:%S"),
		"devid": ldev,
		"linea": linea,
		"window_start": start,
		"window_end": end,
		"http_status": http_status,
		"error": error,
	}


def _http_status(error: Exception):
	"""Código HTTP de la excepción, si lo trae."""
	respuesta = getattr(error, "response", None)
	return getattr(respuesta, "status_code", None)


def _estado_linea(rows, caidas, cobertura, desajustes_reloj=()) -> str:
	"""Clasifica el resultado de una línea con más matiz que un booleano.

	`rows > 0` no distinguía una línea legítimamente detenida (que reporta
	cero) de una respuesta truncada (que reporta una fila).

	`RELOJ_INESPERADO` va por encima de la cobertura porque un reloj corrido
	explica la cobertura rara, no al revés.
	"""
	if not rows:
		return "SIN_DATOS"
	if len(rows) in LIMITES_SOSPECHOSOS:
		return "TRUNCADO_SOSPECHOSO"
	if desajustes_reloj:
		return "RELOJ_INESPERADO"
	if caidas:
		return "COBERTURA_BAJA"
	if cobertura and min(cobertura.values()) <= 0:
		return "COBERTURA_BAJA"
	return "OK"


def _pct_cobertura(cobertura) -> float:
	"""Cobertura media de los días operativos completos de la ventana.

	Cada día aporta su propia duración al denominador: en los dos cambios de
	hora del año el día operativo tiene 23 o 25 horas, y medirlo contra 1440
	fijo delataba una caída de cobertura que no existió.
	"""
	if not cobertura:
		return 0.0
	esperados = sum(
		minutos_dia_operativo(datetime.strptime(fecha, "%Y-%m-%d").date())
		for fecha in cobertura
	)
	return round(100 * sum(cobertura.values()) / esperados, 2) if esperados else 0.0


def _partition_path(base_dir: Path, year: int, month: int, line_name: str) -> Path:
	return (
		base_dir
		/ str(year)
		/ f"{month:02d}"
		/ f"produccion_{year}_{month_name_es(month)}_{safe_filename(line_name)}.csv"
	)


def _resolve_single_line(devid: str) -> dict:
	"""Busca el nombre de la línea en config/lineas.json a partir del devid.

	Sin esto el archivo se nombraría con el devid y el transformer, que busca
	por nombre de línea, no lo levantaría.
	"""
	for linea in load_lineas():
		if str(linea.get("devid")) == str(devid):
			return linea
	print(f"Aviso: devid {devid} no está en config/lineas.json; se usará el devid como nombre de línea.")
	return {"name": str(devid), "devid": devid}


def _write_line(
	rows: Sequence[Mapping],
	base_dir: Path,
	line_name: str,
	partitions: Sequence[tuple[int, int]],
	window_start: datetime,
	window_end: datetime,
	ctx: cdc.Contexto | None = None,
	ausentes: set[str] | None = None,
) -> tuple[int, list[str], list[cdc.Cambio], dict[str, int]]:
	"""Reparte las filas de una línea entre las particiones de la ventana.

	Se recorren todas las particiones de la ventana, incluso las que no
	recibieron filas: así el reemplazo por rango también borra lo que la API
	dejó de reportar.

	Antes de escribir cada partición se compara lo que la API devuelve contra
	lo que el archivo ya tenía: ese diff es el libro de auditoría. La lectura
	previa se reutiliza para el diff y para la escritura, de modo que el
	archivo se lee una sola vez.
	"""
	by_month, undated = partition_by_month(rows)
	if undated:
		print(f"\nAviso: {len(undated)} filas de {line_name} sin 'start' interpretable; se escriben en {partitions[0][0]}-{partitions[0][1]:02d}.")
		by_month.setdefault(partitions[0], []).extend(undated)

	# Las particiones de la ventana se visitan siempre (para que el reemplazo
	# por rango borre lo que la API dejó de reportar), más cualquier partición
	# que la respuesta traiga de más: un segmento que arranca justo en el corte
	# del día 1 cae en el mes siguiente y no puede quedar sin escribir.
	a_escribir = sorted(set(partitions) | set(by_month))

	detalle = []
	cambios: list[cdc.Cambio] = []
	resumen: dict[str, int] = {}
	for year, month in a_escribir:
		path = _partition_path(base_dir, year, month, line_name)
		nuevas = by_month.get((year, month), [])
		previas = read_csv_rows(path)

		if ctx is not None:
			resultado = cdc.comparar(
				previas, nuevas, ctx, window_start, window_end, ausentes_conocidas=ausentes
			)
			cambios.extend(resultado.cambios)
			for clave, valor in resultado.resumen.items():
				resumen[clave] = resumen.get(clave, 0) + valor

			# La traza viaja en la propia fila: desde el dato se llega al libro,
			# y se puede medir cuánto tardaron en asignar el código.
			nuevas = traza.anotar(nuevas, previas, resultado.cambios, ctx)
			previas = traza.rellenar_claves(previas, ctx.devid)

		stats = write_window_csv(nuevas, path, window_start, window_end, existing_rows=previas)
		detalle.append(f"{year}-{month:02d}:{stats['nuevas']}(+{stats['conservadas']} conservadas)")
	return len(rows), detalle, cambios, resumen


def parse_args(argv: Iterable[str] | None = None):
	p = argparse.ArgumentParser(description="Descarga la ventana móvil del reporte sabana.")
	p.add_argument("--start-date", required=False, help="Override inicio (default: automático últimos N días)")
	p.add_argument("--end-date", required=False, help="Override fin (default: automático últimos N días)")
	p.add_argument("--devid", required=False, help="ID de la línea/máquina (single-line)")
	p.add_argument("--output", required=False, help="Directorio base de salida (default: PATH_RAW)")
	p.add_argument("--logs-dir", required=False, help="Directorio de logs plan/resultados (default: PATH_LOGS)")
	p.add_argument("--no-progress", action="store_true", help="Oculta barra de progreso en multi-línea")
	p.add_argument(
		"--days",
		type=int,
		required=False,
		default=EXTRACTOR_WINDOW_DAYS,
		help=f"Cantidad de días hacia atrás, incluyendo ayer (default: {EXTRACTOR_WINDOW_DAYS})",
	)
	p.add_argument("--audit-dir", required=False, help="Directorio del libro de auditoría (default: PATH_AUDIT)")
	p.add_argument("--no-audit", action="store_true", help="Desactiva la detección de cambios (no recomendado)")
	p.add_argument("--intentos", type=int, default=3, help="Intentos por línea ante fallos de red (default: 3)")
	p.add_argument("--snapshots-dir", required=False, help="Directorio del landing inmutable (default: PATH_SNAPSHOTS)")
	p.add_argument("--no-snapshots", action="store_true", help="No guarda la respuesta cruda (no recomendado)")
	return p.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
	args = parse_args(argv)
	window_start, window_end, start, end = _resolve_window(args)
	partitions = month_partitions(window_start, window_end)

	base_dir = Path(args.output) if args.output else Path(PATHS.get("raw", "data/raw"))
	logs_dir = Path(args.logs_dir) if args.logs_dir else Path(PATHS.get("logs", "logs"))
	audit_dir = Path(args.audit_dir) if args.audit_dir else Path(PATHS.get("audit", "data/audit"))
	snapshots_dir = (
		Path(args.snapshots_dir) if args.snapshots_dir
		else Path(PATHS.get("snapshots", "data/snapshots"))
	)
	stamp = run_stamp()
	run_id = nuevo_run_id(stamp)
	detectado_en = now_planta()

	auditar = not args.no_audit
	guardar_crudo = not args.no_snapshots
	ausentes = claves_ausentes(audit_dir) if auditar else set()
	corridas = leer_ultimas_corridas(audit_dir) if auditar else {}

	# Se informan los días efectivos de la ventana, no el valor de --days: con
	# fechas explícitas los dos no coinciden y el encabezado mentía.
	dias_ventana = (window_end - window_start).total_seconds() / 86400
	print(f"Ventana de {dias_ventana:.6g} días operativos (corte {corte_utc(a_local(window_end).date())} UTC)")
	print(f"  consulta a la API (UTC): {start}  ->  {end}")
	print(f"  reloj de planta        : {window_start:%Y-%m-%d %H:%M:%S}  ->  {window_end:%Y-%m-%d %H:%M:%S}")
	print(f"Particiones a escribir: {', '.join(f'{y}-{m:02d}' for y, m in partitions)}")
	print(f"run_id: {run_id}" + ("" if auditar else "  (auditoría desactivada)"))

	lineas = [_resolve_single_line(args.devid)] if args.devid else load_lineas()
	if not lineas:
		print("No se encontraron líneas para procesar. Finalizando.")
		return 1

	plan_items = [
		{
			"line": linea.get("name", linea.get("devid")),
			"devid": linea.get("devid"),
			"start": start,
			"end": end,
			"output_path": " | ".join(
				str(_partition_path(base_dir, y, m, linea.get("name", linea.get("devid"))))
				for y, m in partitions
			),
		}
		for linea in lineas
	]
	plan_log = write_plan_log(plan_items, logs_dir, stamp)
	print(f"Plan registrado: {plan_log}")

	print("Obteniendo token...")
	sesion = Sesion(intentos=args.intentos)
	sesion.token_vigente()

	print(f"Procesando {len(lineas)} líneas ...")
	results_items = []
	manifest_items: list[dict] = []
	global_count = 0
	cambios_totales: list[cdc.Cambio] = []
	resumen_global: dict[str, int] = {}
	caidas_totales: list[dict] = []
	desajustes_totales: list[reloj.Desajuste] = []
	for linea in progress(lineas, len(lineas), enabled=not args.no_progress):
		ldev = linea.get("devid")
		lname = linea.get("name", ldev)
		snapshot = ruta_snapshot(snapshots_dir, run_id, ldev, detectado_en)
		try:
			data = sesion.sabana(start, end, ldev)

			# El landing va ANTES de cualquier normalización: es la única copia
			# de lo que la API respondió realmente.
			if guardar_crudo:
				escribir_snapshot(
					snapshot,
					_sobre(run_id, detectado_en, lname, ldev, start, end, http_status=200),
					data,
				)
			rows = infer_rows(data)

			# El testigo del modelo temporal viaja en la propia respuesta: si
			# `duration` no cuadra con el intervalo, el reloj del proveedor se
			# movió donde no lo esperábamos. Se comprueba antes de escribir.
			desajustes_reloj = reloj.desajustes(rows, lname)
			desajustes_totales.extend(desajustes_reloj)

			ctx = cdc.Contexto(
				run_id=run_id,
				devid=ldev,
				linea=lname,
				detectado_en=detectado_en,
				run_id_anterior=corridas.get(str(lname), ""),
			) if auditar else None

			count, detalle, cambios, resumen = _write_line(
				rows, base_dir, lname, partitions, window_start, window_end, ctx, ausentes
			)
			global_count += count

			cobertura, caidas = {}, []
			if auditar:
				cambios_totales.extend(cambios)
				for clave, valor in resumen.items():
					resumen_global[clave] = resumen_global.get(clave, 0) + valor
				cobertura = cobertura_por_dia(rows, window_start, window_end)
				caidas = registrar_cobertura(lname, ldev, cobertura, audit_dir, run_id, detectado_en)
				caidas_totales.extend(caidas)
				corridas[str(lname)] = run_id
				detalle.append(_resumen_corto(resumen))

			estado = _estado_linea(rows, caidas, cobertura, desajustes_reloj)
			manifest_items.append({
				**_sobre(run_id, detectado_en, lname, ldev, start, end, http_status=200),
				"estado": estado,
				"n_filas": count,
				"cobertura_pct": _pct_cobertura(cobertura),
				"cambios": resumen.get("filas_libro", 0) if auditar else None,
				"snapshot": str(snapshot),
			})
			results_items.append({
				"line": lname,
				"devid": ldev,
				"start": start,
				"end": end,
				"rows": count,
				# Éxito es que la extracción se completó. Si la línea estuvo
				# detenida y no reportó nada, eso es un dato, no un fallo.
				"success": True,
				"estado": estado,
				"cobertura_pct": _pct_cobertura(cobertura),
				"error": "",
				"detalle": "; ".join(detalle),
			})
		except Exception as e:  # noqa: BLE001
			# Una corrida fallida es justamente la que interesa poder
			# reconstruir, así que también deja constancia en el landing.
			if guardar_crudo and not snapshot.exists():
				try:
					escribir_snapshot(
						snapshot,
						_sobre(run_id, detectado_en, lname, ldev, start, end,
						       http_status=_http_status(e), error=str(e)),
						None,
					)
				except OSError:
					pass
			manifest_items.append({
				**_sobre(run_id, detectado_en, lname, ldev, start, end,
				         http_status=_http_status(e), error=str(e)),
				"estado": "ERROR",
				"n_filas": 0,
				"cobertura_pct": 0.0,
				"cambios": 0,
				"snapshot": str(snapshot),
			})
			# Una línea que falla no escribe nada: sus archivos quedan como estaban.
			results_items.append({
				"line": lname,
				"devid": ldev,
				"start": start,
				"end": end,
				"rows": 0,
				"success": False,
				"estado": "ERROR",
				"cobertura_pct": 0.0,
				"error": str(e),
				"detalle": "",
			})

	results_log = write_results_log(results_items, logs_dir, stamp)
	print(f"Resultados registrados: {results_log}")

	if guardar_crudo:
		manifiesto = append_manifest(manifest_items, snapshots_dir)
		purgados = rotar_snapshots(snapshots_dir, RETENCION_SNAPSHOTS_DIAS)
		pesados = sum(
			Path(i["snapshot"]).stat().st_size
			for i in manifest_items if Path(i["snapshot"]).exists()
		)
		print(
			f"Landing: {len(manifest_items)} snapshots ({pesados/1024:.0f} KB) | "
			f"manifiesto: {manifiesto}"
			+ (f" | {purgados} purgados por retención" if purgados else "")
		)

	if auditar:
		libro = append_cambios(cambios_totales, audit_dir)
		guardar_ultimas_corridas(corridas, audit_dir)
		_reportar_auditoria(cambios_totales, resumen_global, caidas_totales, libro, desajustes_totales)

	print(f"Finalizado. Total filas acumuladas: {global_count}")

	por_estado = {}
	for r in results_items:
		por_estado.setdefault(r["estado"], []).append(r)
	print("Estado por línea: " + ", ".join(
		f"{estado}={len(rs)}" for estado, rs in sorted(por_estado.items())
	))
	if sesion.reintentos or sesion.renovaciones:
		print(f"  ({sesion.reintentos} reintentos de red, {sesion.renovaciones} renovaciones de token)")

	for estado in ("ERROR", "TRUNCADO_SOSPECHOSO", "COBERTURA_BAJA", "SIN_DATOS"):
		for r in por_estado.get(estado, []):
			detalle = r["error"] or f"cobertura {r['cobertura_pct']}%"
			print(f" - {estado}: {r['line']} (devid={r['devid']}) {detalle}")

	rotar_logs(logs_dir, RETENCION_LOGS_DIAS)
	fallidas = len(por_estado.get("ERROR", []))
	return 1 if fallidas == len(results_items) else 0


def _resumen_corto(resumen: Mapping[str, int]) -> str:
	"""Resumen de cambios de una línea para el results_*.csv."""
	partes = [
		f"{tipo.lower()}={resumen.get(tipo, 0)}"
		for tipo in (cdc.REASIGNACION, cdc.RECORTE_SEGMENTO, cdc.DESAPARECIDO, cdc.OTRO_CAMBIO)
		if resumen.get(tipo, 0)
	]
	return "cambios: " + (", ".join(partes) if partes else "ninguno")


def _reportar_auditoria(
	cambios: Sequence[cdc.Cambio],
	resumen: Mapping[str, int],
	caidas: Sequence[Mapping],
	libro: Path,
	desajustes_totales: Sequence[reloj.Desajuste] = (),
) -> None:
	"""Imprime lo que la corrida detectó. Es la señal que hoy no existe."""
	reasignaciones = [c for c in cambios if cdc.es_reasignacion_de_codigo(c)]
	print("\n--- Auditoría de cambios ---")
	print(
		f"Segmentos: {resumen.get('nuevas', 0)} recibidos, "
		f"{resumen.get('NUEVO', 0)} nuevos, {resumen.get('previas_en_ventana', 0)} ya conocidos"
	)
	print(
		f"Cambios: {resumen.get(cdc.REASIGNACION, 0)} reasignaciones, "
		f"{resumen.get(cdc.OTRO_CAMBIO, 0)} otras ediciones, "
		f"{resumen.get(cdc.RECORTE_SEGMENTO, 0)} recortes, "
		f"{resumen.get(cdc.DESAPARECIDO, 0)} desaparecidos, "
		f"{resumen.get(cdc.REAPARECIDO, 0)} reaparecidos"
	)
	if resumen.get("colisiones_clave"):
		print(
			f"Nota: {resumen['colisiones_clave']} segmentos comparten instante con otro "
			"(duración cero); se auditan como grupo."
		)

	if reasignaciones:
		print(f"\nReasignaciones de código ({len(reasignaciones)}):")
		for c in reasignaciones[:15]:
			print(
				f"  {c.linea} {c.start} [{c.dias_desde_evento}d] {c.campo}: "
				f"{c.valor_anterior or '(vacío)'} -> {c.valor_nuevo or '(vacío)'}"
			)
		if len(reasignaciones) > 15:
			print(f"  ... y {len(reasignaciones) - 15} más en el libro")

	if desajustes_totales:
		print(
			f"\nALERTA DE RELOJ: {len(desajustes_totales)} segmentos en que `duration` no "
			"cuadra con el intervalo."
		)
		for d in desajustes_totales[:10]:
			print(f"  {d}")
		if len(desajustes_totales) > 10:
			print(f"  ... y {len(desajustes_totales) - 10} más")
		print(f"  {reloj.diagnostico(desajustes_totales)}")

	if resumen.get(cdc.DESAPARECIDO, 0):
		print(f"\nALERTA: {resumen[cdc.DESAPARECIDO]} segmentos dejaron de ser reportados por la API.")

	if caidas:
		perdidos = sum(c["delta"] for c in caidas)
		print(f"\nALERTA de cobertura: {len(caidas)} días perdieron tiempo reportado ({perdidos:.0f} min en total)")
		for c in sorted(caidas, key=lambda x: x["delta"])[:10]:
			print(f"  {c['linea']} {c['fecha']}: {c['minutos_antes']:.0f} -> {c['minutos_ahora']:.0f} min ({c['delta']:+.0f})")

	print(f"\nLibro de auditoría: {libro} (+{len(cambios)} filas)")


if __name__ == "__main__":  # pragma: no cover
	try:
		raise SystemExit(main())
	except KeyboardInterrupt:
		print("Interrumpido por el usuario.")
		raise SystemExit(130)
