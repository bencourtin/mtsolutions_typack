
import os
import sys
import re
import json
import time
import warnings
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from calendar import monthrange

import polars as pl
from polars.exceptions import PolarsWarning, DataOrientationWarning

# Ajusta el PYTHONPATH al root del repo
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_dir not in sys.path:
    sys.path.append(root_dir)

# Importaciones locales: se asume tu misma estructura
from services.auth import get_token
from services.reports import get_sabana_report

# Silenciar warnings de Polars como en tu código original
warnings.filterwarnings("ignore", category=PolarsWarning)
warnings.filterwarnings("ignore", category=DataOrientationWarning)


class Extractor:
    """
    Flujo:
    1) Detecta última fecha disponible por línea en data/extractor/output
    2) Genera un plan (log) de todas las descargas pendientes hasta AYER (respecto de run_date)
    3) Ejecuta descargas y guarda CSVs por carpeta (año/mes)
    4) Deja logs de plan y de resultado en data/extractor/logs
    """

    FILE_REGEX = re.compile(r"produccion_(\d{4}-\d{2}-\d{2})_")

    def __init__(self, data_dir: str | None = None, tz: str = "America/Santiago"):
        self.tz = ZoneInfo(tz)
        self.base_dir = os.path.dirname(__file__)
        self.config = self._get_config()
        self.data_dir = self._ensure_data_dir(data_dir)
        self.logs_dir = os.path.join(self.data_dir, "logs")
        os.makedirs(self.logs_dir, exist_ok=True)
        self.lines = self.config.get("lineas", [])
        self.output_dir = self.config.get("output_dir", self.data_dir)

    # ---------------------------
    # Config y estructura de dirs
    # ---------------------------

    def _get_config(self) -> dict:
        """Lee la configuración completa desde config/config.json."""
        config_path = os.path.join(os.path.dirname(self.base_dir), "config", "config.json")
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[WARN] No se pudo leer {config_path}: {e}")
            return {}

    def _ensure_data_dir(self, custom: str | None) -> str:
        """Crea (si no existe) el directorio raíz de datos."""
        if custom:
            os.makedirs(custom, exist_ok=True)
            return custom
        data_dir = os.path.join(self.base_dir, "data")
        os.makedirs(data_dir, exist_ok=True)
        return data_dir

    def _get_lines_config(self) -> list[dict]:
        """Lee la configuración de líneas desde config/lineas.json."""
        config_path = os.path.join(self.base_dir, "config", "lineas.json")
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            return config.get("lineas", [])
        except Exception as e:
            print(f"[WARN] No se pudo leer {config_path}: {e}")
            return []

    def _line_output_root(self, line_name: str) -> str:
        return os.path.join(self.output_dir, line_name)

    def _out_dir_for(self, line_name: str, d: date) -> str:
        return os.path.join(self._line_output_root(line_name),
                            f"{d.year:04d}", f"{d.month:02d}")

    def _safe_line_name(self, name: str) -> str:
        return "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in name)

    def _filename_for(self, line_name: str, d: date) -> str:
        safe = self._safe_line_name(line_name)
        return f"produccion_{d:%Y-%m-%d}_{safe}.csv"

    # ---------------------------
    # Escaneo de últimas fechas
    # ---------------------------

    def _parse_date_from_filename(self, filename: str) -> date | None:
        m = self.FILE_REGEX.search(filename)
        if not m:
            return None
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except ValueError:
            return None

    def last_date_for_line(self, line_name: str) -> date | None:
        """
        Busca en {output_dir}/{linea}/*/* todos los .csv con patrón y retorna la última fecha.
        Si no hay archivos, retorna None.
        """
        root = self._line_output_root(line_name)
        if not os.path.isdir(root):
            return None

        latest: date | None = None
        for year in os.listdir(root):
            ypath = os.path.join(root, year)
            if not (year.isdigit() and os.path.isdir(ypath)):
                continue
            for month in os.listdir(ypath):
                mpath = os.path.join(ypath, month)
                if not (month.isdigit() and os.path.isdir(mpath)):
                    continue
                for fname in os.listdir(mpath):
                    if not fname.endswith(".csv"):
                        continue
                    d = self._parse_date_from_filename(fname)
                    if d and (latest is None or d > latest):
                        latest = d
        return latest

    # ---------------------------
    # Planificación (log de plan)
    # ---------------------------

    def build_plan(self, run_date: date,
                   first_date_if_empty: date | None = None) -> list[dict]:
        """
        Genera el listado de todas las fechas a descargar por línea.
        Descarga desde (ultima_fecha + 1) hasta (run_date - 1).
        Si no existe última fecha:
           - si first_date_if_empty se provee: desde esa fecha hasta (run_date - 1)
           - si no: solo (run_date - 1)
        """
        target_end = run_date - timedelta(days=1)
        plan: list[dict] = []

        for line in self.lines:
            line_name = line["name"]
            last_d = self.last_date_for_line(line_name)
            if last_d is None:
                start_d = first_date_if_empty or target_end
            else:
                start_d = last_d + timedelta(days=1)

            if start_d > target_end:
                # Nada pendiente
                continue

            d = start_d
            while d <= target_end:
                out_dir = self._out_dir_for(line_name, d)
                out_path = os.path.join(out_dir, self._filename_for(line_name, d))
                plan.append({
                    "line_name": line_name,
                    "devid": line.get("devid"),
                    "date": d,
                    "out_dir": out_dir,
                    "out_path": out_path
                })
                d += timedelta(days=1)

        # Guardar log de plan
        ts = datetime.now(self.tz).strftime("%Y%m%d_%H%M%S")
        plan_csv = os.path.join(self.logs_dir, f"plan_{ts}.csv")
        if plan:
            df_plan = pl.DataFrame([{
                "line": p["line_name"],
                "devid": p["devid"],
                "date": p["date"].strftime("%Y-%m-%d"),
                "output_path": p["out_path"]
            } for p in plan])
            df_plan.write_csv(plan_csv, separator=";")
        else:
            # Crear un archivo mínimo para dejar constancia de que no había pendientes
            with open(plan_csv, "w", encoding="utf-8") as f:
                f.write("line;devid;date;output_path\n")
        print(f"[INFO] Plan guardado en: {plan_csv}")
        return plan

    # ---------------------------
    # Descarga y guardado
    # ---------------------------
 
    def _polars_csv_from_data(self, datos: list[dict]) -> pl.DataFrame:
        df = pl.DataFrame(datos)
        # Convertir floats para que usen coma decimal y vacío para None (igual que tu código)
        float_cols = [c for c, dt in zip(df.columns, df.dtypes) if dt in (pl.Float64, pl.Float32)]
        for col in float_cols:
            df = df.with_columns(
                pl.col(col).map_elements(lambda x: "" if x is None else str(x).replace(".", ",")).alias(col)
            )
        return df

    def _download_one(self, line: dict, target_date: date, token: str, quiet: bool = True) -> tuple[bool, str | None]:
        name = line["name"]
        devid = line["devid"]
        start = f"{target_date:%Y-%m-%d} 00:00:00"
        end   = f"{target_date:%Y-%m-%d} 23:59:59"
        try:
            datos = get_sabana_report(token, start=start, end=end, devid=devid)
            if not datos:
                return False, "Respuesta vacía del servicio"
            df = self._polars_csv_from_data(datos)
            out_dir = self._out_dir_for(name, target_date)
            os.makedirs(out_dir, exist_ok=True)
            out_path = os.path.join(out_dir, self._filename_for(name, target_date))
            df.write_csv(out_path, separator=";")
            return True, None
        except Exception as e:
            if not quiet:
                print(f"[{name} {target_date}] Error: {e}")
            return False, str(e)

    def execute_plan(self, plan: list[dict], token_renew_every: int = 50, sleep_between_batches: float = 2.0) -> str:
        """
        Ejecuta el plan secuencialmente, renueva token cada N descargas.
        Devuelve la ruta del log de resultados.
        """
        if not plan:
            # Dejar constancia de ejecución sin pendientes
            ts = datetime.now(self.tz).strftime("%Y%m%d_%H%M%S")
            results_csv = os.path.join(self.logs_dir, f"results_{ts}.csv")
            with open(results_csv, "w", encoding="utf-8") as f:
                f.write("line;date;success;error\n")
            print("[INFO] No hay descargas pendientes.")
            return results_csv

        token = get_token()
        results: list[dict] = []
        for i, item in enumerate(plan, start=1):
            if (i - 1) % token_renew_every == 0 and i > 1:
                token = get_token()
            ok, err = self._download_one(
                {"name": item["line_name"], "devid": item["devid"]},
                item["date"],
                token,
                quiet=True
            )
            results.append({
                "line": item["line_name"],
                "date": item["date"].strftime("%Y-%m-%d"),
                "success": ok,
                "error": "" if err is None else err
            })
            # Pequeña pausa para no saturar
            time.sleep(sleep_between_batches)

        ts = datetime.now(self.tz).strftime("%Y%m%d_%H%M%S")
        results_csv = os.path.join(self.logs_dir, f"results_{ts}.csv")
        pl.DataFrame(results).write_csv(results_csv, separator=";")
        print(f"[INFO] Resultados guardados en: {results_csv}")
        # Resumen en consola
        fails = [r for r in results if not r["success"]]
        if fails:
            print("\n=== Fallos detectados ===")
            for f in fails:
                print(f"Línea: {f['line']}, Fecha: {f['date']}, Error: {f['error']}")
        else:
            print("\nTodos los archivos procesados correctamente.")
        return results_csv

    # ---------------------------
    # API de alto nivel
    # ---------------------------

    def run(self,
            run_date: date | None = None,
            first_date_if_empty: date | None = None,
            dry_run: bool = False) -> tuple[str, str | None]:
        """
        Ejecuta el flujo completo.
        - run_date: fecha de ejecución (por defecto, hoy en la TZ configurada). Se descarga hasta run_date-1.
        - first_date_if_empty: si una línea no tiene datos locales, arrancar desde esta fecha; si None, sólo ayer.
        - dry_run: si True, sólo genera el plan, no descarga.
        Devuelve (plan_csv_path, results_csv_path or None)
        """
        if run_date is None:
            run_date = datetime.now(self.tz).date()

        print(f"\nIniciando extracción (run_date={run_date}, descargar hasta {run_date - timedelta(days=1)})")
        print(f"Líneas configuradas: {len(self.lines)}")

        plan = self.build_plan(run_date=run_date, first_date_if_empty=first_date_if_empty)
        if dry_run:
            return (self._latest_log_path(prefix="plan_"), None)
        results_csv = self.execute_plan(plan)
        return (self._latest_log_path(prefix="plan_"), results_csv)

    def _latest_log_path(self, prefix: str) -> str | None:
        files = [f for f in os.listdir(self.logs_dir) if f.startswith(prefix)]
        if not files:
            return None
        files.sort(reverse=True)
        return os.path.join(self.logs_dir, files[0])


def parse_args():
    import argparse
    p = argparse.ArgumentParser(description="Extractor diario con planificación y logs.")
    p.add_argument("--data-dir", help="Directorio raíz de datos (default: ./data)", default=None)
    p.add_argument("--run-date", help="Fecha de ejecución (YYYY-MM-DD). Descarga hasta el día anterior.", default=None)
    p.add_argument("--first-date-if-empty", help="Si una línea no tiene datos, empezar desde aquí (YYYY-MM-DD).", default=None)
    p.add_argument("--dry-run", action="store_true", help="Sólo generar el plan, sin descargar.")
    p.add_argument("--tz", default="America/Santiago", help="Timezone para la fecha de ejecución (default: America/Santiago).")
    return p.parse_args()


def main():
    args = parse_args()
    run_date = None
    if args.run_date:
        run_date = datetime.strptime(args.run_date, "%Y-%m-%d").date()

    first_date_if_empty = None
    if args.first_date_if_empty:
        first_date_if_empty = datetime.strptime(args.first_date_if_empty, "%Y-%m-%d").date()

    extractor = Extractor(data_dir=args.data_dir, tz=args.tz)
    plan_csv, results_csv = extractor.run(run_date=run_date,
                                          first_date_if_empty=first_date_if_empty,
                                          dry_run=args.dry_run)
    print("\n=== RESUMEN ===")
    print(f"Plan:    {plan_csv}")
    if results_csv:
        print(f"Resultados: {results_csv}")
    else:
        print("Resultados: (dry-run, no se descargó nada)")

if __name__ == "__main__":
    main()