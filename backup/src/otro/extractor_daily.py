"""
Extractor diario incremental refactorizado con funciones modulares.
Optimizado para reutilización de código y robustez.
"""
import os
import sys
import warnings
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, Tuple

# Silenciar warnings de Polars cuando se importe
try:
    from polars.exceptions import PolarsWarning, DataOrientationWarning
    warnings.filterwarnings("ignore", category=PolarsWarning)
    warnings.filterwarnings("ignore", category=DataOrientationWarning)
except ImportError:
    # Polars no disponible, continuar sin warnings
    pass

# Ajustar PYTHONPATH para importaciones locales
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

# Importaciones de módulos modulares refactorizados
from functions.config_manager import ConfigManager, PathManager
from functions.date_scanner import DateScanner
from functions.plan_manager import PlanManager
from functions.downloader import Downloader, BatchDownloader
from functions.log_utils import LogUtils


class DailyExtractor:
    """
    Extractor diario incremental refactorizado.
    
    Flujo optimizado:
    1) Detecta última fecha disponible por línea
    2) Genera un plan de todas las descargas pendientes hasta ayer
    3) Ejecuta descargas y guarda CSVs organizados por año/mes
    4) Registra logs de plan y resultados
    
    Características:
    - Funciones modulares reutilizables
    - Manejo robusto de errores
    - Logging detallado
    - Sistema de reintentos
    - Limpieza automática de logs antiguos
    """

    def __init__(self, data_dir: Optional[str] = None, tz: str = "America/Santiago"):
        """
        Inicializa el extractor diario.
        
        Args:
            data_dir: Directorio base para datos (opcional)
            tz: Zona horaria para operaciones
        """
        self.tz = ZoneInfo(tz)
        self.base_dir = current_dir
        
        # Inicializar gestores modulares
        self.config_manager = ConfigManager(self.base_dir)
        self.config = self.config_manager.get_config()
        self.data_dir = self.config_manager.ensure_data_dir(data_dir)
        self.logs_dir = LogUtils.ensure_logs_dir(os.path.join(self.data_dir, "logs"))
        self.lines = self.config.get("lineas", [])
        self.output_dir = self.config.get("output_dir", self.data_dir)
        
        # Inicializar componentes especializados
        self.path_manager = PathManager(self.output_dir)
        self.date_scanner = DateScanner(self.path_manager)
        self.plan_manager = PlanManager(self.path_manager, self.date_scanner, 
                                      self.logs_dir, self.tz)
        self.downloader = Downloader(self.path_manager)
        self.batch_downloader = BatchDownloader(self.downloader, self.logs_dir, self.tz)

    def run(self, run_date: Optional[date] = None, first_date_if_empty: Optional[date] = None,
            dry_run: bool = False) -> Tuple[Optional[str], Optional[str]]:
        """
        Ejecuta el flujo completo de extracción incremental.
        
        Args:
            run_date: Fecha de ejecución (default: hoy). Se descarga hasta run_date-1
            first_date_if_empty: Si una línea no tiene datos, arrancar desde esta fecha
            dry_run: Si True, sólo genera el plan, no descarga
            
        Returns:
            Tuple con rutas de archivos de plan y resultados
        """
        if run_date is None:
            run_date = datetime.now(self.tz).date()

        target_date = run_date - timedelta(days=1)
        
        print(f"\n📅 EXTRACTOR DIARIO INCREMENTAL")
        print(f"🎯 Fecha objetivo: hasta {target_date}")
        print(f"📊 Líneas configuradas: {len(self.lines)}")

        # Mostrar resumen de última fecha por línea
        print("\n📋 Estado actual por línea:")
        for line in self.lines[:5]:  # Mostrar solo las primeras 5
            last_date = self.date_scanner.last_date_for_line(line["name"])
            status = f"Última: {last_date}" if last_date else "Sin datos"
            print(f"  {line['name']:12} | {status}")
        if len(self.lines) > 5:
            print(f"  ... y {len(self.lines) - 5} líneas más")

        # Generar plan incremental
        plan = self.plan_manager.build_incremental_plan(
            lines=self.lines, 
            run_date=run_date, 
            first_date_if_empty=first_date_if_empty
        )
        
        if dry_run:
            print("🧪 Modo dry-run: solo generando plan")
            return (LogUtils.latest_log_path(self.logs_dir, "plan_incremental"), None)
        
        # Ejecutar descargas
        results_csv = self.batch_downloader.execute_plan(
            plan=plan,
            token_renew_every=50,
            sleep_between=2.0,
            max_retries=3
        )
        
        # Limpiar logs antiguos
        LogUtils.cleanup_old_logs(self.logs_dir, "plan_incremental", keep_count=10)
        LogUtils.cleanup_old_logs(self.logs_dir, "results", keep_count=20)
        
        return (LogUtils.latest_log_path(self.logs_dir, "plan_incremental"), results_csv)

    def get_status_summary(self) -> dict:
        """
        Retorna un resumen del estado actual de todas las líneas.
        
        Returns:
            Dict con información de última fecha y estado por línea
        """
        summary = {}
        
        for line in self.lines:
            line_name = line["name"]
            last_date = self.date_scanner.last_date_for_line(line_name)
            
            if last_date:
                days_behind = (datetime.now(self.tz).date() - timedelta(days=1) - last_date).days
                status = "up-to-date" if days_behind <= 0 else f"{days_behind} días atrás"
            else:
                status = "sin datos"
                days_behind = None
            
            summary[line_name] = {
                "last_date": last_date,
                "days_behind": days_behind,
                "status": status,
                "devid": line.get("devid")
            }
        
        return summary

    def print_status_report(self):
        """Imprime un reporte del estado actual en consola."""
        summary = self.get_status_summary()
        
        print(f"\n📊 REPORTE DE ESTADO - EXTRACTOR DIARIO")
        print("=" * 70)
        
        up_to_date = 0
        behind = 0
        no_data = 0
        
        for line_name, info in summary.items():
            if info["status"] == "up-to-date":
                status_icon = "✅"
                up_to_date += 1
            elif info["status"] == "sin datos":
                status_icon = "❌"
                no_data += 1
            else:
                status_icon = "⚠️"
                behind += 1
            
            last_date_str = info["last_date"].strftime("%Y-%m-%d") if info["last_date"] else "N/A"
            print(f"{status_icon} {line_name:12} | Última: {last_date_str:10} | {info['status']}")
        
        print("=" * 70)
        print(f"📈 Resumen: {up_to_date} al día, {behind} atrasadas, {no_data} sin datos")


def parse_args():
    """Parsea argumentos de línea de comandos."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Extractor diario incremental para TYPACK ETL",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos de uso:
  %(prog)s                           # Extracción incremental normal
  %(prog)s --dry-run                 # Solo mostrar plan de extracción
  %(prog)s --status                  # Solo mostrar estado actual
  %(prog)s --run-date 2024-01-15     # Usar fecha específica como referencia
  %(prog)s --first-date 2024-01-01   # Para líneas sin datos, empezar desde esta fecha
        """
    )
    
    parser.add_argument("--data-dir", 
                       help="Directorio raíz de datos (default: ./data)")
    parser.add_argument("--run-date", 
                       help="Fecha de referencia YYYY-MM-DD (default: hoy)")
    parser.add_argument("--first-date-if-empty", 
                       help="Si una línea no tiene datos, empezar desde YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true",
                       help="Solo generar plan, no descargar")
    parser.add_argument("--status", action="store_true",
                       help="Solo mostrar estado actual de las líneas")
    parser.add_argument("--tz", default="America/Santiago",
                       help="Zona horaria (default: America/Santiago)")
    
    return parser.parse_args()


def main():
    """Función principal."""
    args = parse_args()
    
    # Parsear fechas si se proporcionan
    run_date = None
    if args.run_date:
        try:
            run_date = datetime.strptime(args.run_date, "%Y-%m-%d").date()
        except ValueError:
            print("❌ Error: Formato de fecha inválido. Use YYYY-MM-DD")
            sys.exit(1)
    
    first_date_if_empty = None
    if args.first_date_if_empty:
        try:
            first_date_if_empty = datetime.strptime(args.first_date_if_empty, "%Y-%m-%d").date()
        except ValueError:
            print("❌ Error: Formato de fecha inicial inválido. Use YYYY-MM-DD")
            sys.exit(1)

    # Crear extractor
    extractor = DailyExtractor(
        data_dir=args.data_dir,
        tz=args.tz
    )
    
    # Ejecutar según modo
    if args.status:
        extractor.print_status_report()
    else:
        plan_csv, results_csv = extractor.run(
            run_date=run_date,
            first_date_if_empty=first_date_if_empty,
            dry_run=args.dry_run
        )
        
        print("\n📋 RESUMEN FINAL")
        print(f"📄 Plan: {plan_csv}")
        if results_csv:
            print(f"📊 Resultados: {results_csv}")
        else:
            print("📊 Resultados: (dry-run, no se ejecutaron descargas)")


if __name__ == "__main__":
    main()