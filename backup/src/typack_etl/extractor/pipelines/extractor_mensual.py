"""
Extractor mensual que obtiene datos de un mes completo específico.
Utiliza funciones modulares reutilizables para mayor robustez.
"""
import os
import sys
import warnings
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from calendar import monthrange
from typing import Optional, Tuple

# Silenciar warnings de Polars
try:
    from polars.exceptions import PolarsWarning, DataOrientationWarning
    warnings.filterwarnings("ignore", category=PolarsWarning)
    warnings.filterwarnings("ignore", category=DataOrientationWarning)
except ImportError:
    pass

# Ajustar PYTHONPATH para importaciones locales
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

# Importar módulos modulares
from functions.config_manager import ConfigManager, PathManager
from functions.date_scanner import DateScanner
from functions.plan_manager import PlanManager
from functions.downloader import Downloader, BatchDownloader
from functions.log_utils import LogUtils


class MonthlyExtractor:
    """
    Extractor mensual completo.
    
    Características:
    - Extrae un mes completo especificado
    - Detección inteligente de archivos existentes
    - Manejo robusto de errores y reintentos
    - Logging detallado de operaciones
    """

    def __init__(self, data_dir: Optional[str] = None, tz: str = "America/Santiago"):
        """
        Inicializa el extractor mensual.
        
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
        
        # Componentes especializados
        self.path_manager = PathManager(self.output_dir)
        self.date_scanner = DateScanner(self.path_manager)
        self.plan_manager = PlanManager(self.path_manager, self.date_scanner, 
                                      self.logs_dir, self.tz)
        self.downloader = Downloader(self.path_manager)
        self.batch_downloader = BatchDownloader(self.downloader, self.logs_dir, self.tz)

    def run_current_month(self, dry_run: bool = False, 
                         overwrite: bool = False) -> Tuple[Optional[str], Optional[str]]:
        """
        Extrae el mes actual hasta ayer.
        
        Args:
            dry_run: Solo generar plan sin descargar
            overwrite: Sobrescribir archivos existentes
            
        Returns:
            Tuple con rutas de archivos de plan y resultados
        """
        today = datetime.now(self.tz).date()
        year = today.year
        month = today.month
        
        # Hasta ayer o último día del mes, lo que sea menor
        last_day_of_month = monthrange(year, month)[1]
        yesterday = today - timedelta(days=1)
        
        if yesterday.month != month:
            # Si ayer era del mes anterior, usar todo el mes actual disponible
            end_day = min(today.day - 1, last_day_of_month)
        else:
            end_day = yesterday.day
        
        if end_day < 1:
            print("❌ No hay días válidos para extraer en el mes actual")
            return None, None
        
        return self.run_month(year, month, end_day=end_day, dry_run=dry_run, overwrite=overwrite)

    def run_month(self, year: int, month: int, end_day: Optional[int] = None,
                  dry_run: bool = False, overwrite: bool = False) -> Tuple[Optional[str], Optional[str]]:
        """
        Extrae un mes específico completo o hasta un día determinado.
        
        Args:
            year: Año del mes a extraer
            month: Mes a extraer (1-12)
            end_day: Último día a extraer (opcional, default: último día del mes)
            dry_run: Solo generar plan sin descargar
            overwrite: Sobrescribir archivos existentes
            
        Returns:
            Tuple con rutas de archivos de plan y resultados
        """
        # Validar entrada
        if not (1 <= month <= 12):
            raise ValueError(f"Mes inválido: {month}. Debe estar entre 1 y 12")
        
        # Calcular rango de fechas
        start_date = date(year, month, 1)
        last_day_of_month = monthrange(year, month)[1]
        
        if end_day is None:
            end_day = last_day_of_month
        else:
            end_day = min(end_day, last_day_of_month)
        
        end_date = date(year, month, end_day)
        
        print(f"\n📅 EXTRACTOR MENSUAL")
        print(f"🗓️  Período: {start_date} → {end_date}")
        print(f"📊 Líneas configuradas: {len(self.lines)}")
        
        # Generar plan usando el plan manager
        plan = self.plan_manager.build_date_range_plan(
            lines=self.lines,
            start_date=start_date,
            end_date=end_date,
            overwrite=overwrite
        )
        
        if dry_run:
            print("🧪 Modo dry-run: solo generando plan")
            return (LogUtils.latest_log_path(self.logs_dir, "plan_range"), None)
        
        # Ejecutar descargas
        results_csv = self.batch_downloader.execute_plan(
            plan=plan,
            token_renew_every=30,
            sleep_between=1.0,
            max_retries=3
        )
        
        # Limpiar logs antiguos
        LogUtils.cleanup_old_logs(self.logs_dir, "plan_range", keep_count=5)
        LogUtils.cleanup_old_logs(self.logs_dir, "results", keep_count=10)
        
        return (LogUtils.latest_log_path(self.logs_dir, "plan_range"), results_csv)

    def get_month_summary(self, year: int, month: int) -> dict:
        """
        Retorna un resumen del estado de un mes específico.
        
        Args:
            year: Año del mes
            month: Mes (1-12)
            
        Returns:
            Dict con estadísticas del mes por línea
        """
        start_date = date(year, month, 1)
        last_day = monthrange(year, month)[1]
        end_date = date(year, month, last_day)
        
        summary = {}
        
        for line in self.lines:
            line_name = line["name"]
            existing_dates = []
            missing_dates = []
            
            current_date = start_date
            while current_date <= end_date:
                if self.date_scanner.has_data_for_date(line_name, current_date):
                    existing_dates.append(current_date)
                else:
                    missing_dates.append(current_date)
                current_date += timedelta(days=1)
            
            coverage_pct = (len(existing_dates) / last_day) * 100
            
            summary[line_name] = {
                "total_days": last_day,
                "existing_files": len(existing_dates),
                "missing_files": len(missing_dates),
                "coverage_percent": round(coverage_pct, 1),
                "month_year": f"{year:04d}-{month:02d}",
                "first_day": start_date,
                "last_day": end_date
            }
        
        return summary

    def print_month_report(self, year: int, month: int):
        """Imprime un reporte del estado del mes en consola."""
        summary = self.get_month_summary(year, month)
        
        print(f"\n📊 REPORTE MENSUAL - {year:04d}-{month:02d}")
        print("=" * 60)
        
        for line_name, stats in summary.items():
            status = "✅" if stats["coverage_percent"] == 100 else "⚠️" if stats["coverage_percent"] > 80 else "❌"
            print(f"{status} {line_name:12} | {stats['existing_files']:2}/{stats['total_days']:2} días | {stats['coverage_percent']:5.1f}%")
        
        total_files = sum(s["existing_files"] for s in summary.values())
        total_possible = sum(s["total_days"] for s in summary.values())
        overall_coverage = (total_files / total_possible) * 100 if total_possible > 0 else 0
        
        print("=" * 60)
        print(f"📈 Cobertura general: {total_files}/{total_possible} archivos ({overall_coverage:.1f}%)")


def parse_args():
    """Parsea argumentos de línea de comandos."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Extractor mensual para TYPACK ETL",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos de uso:
  %(prog)s                           # Extraer mes actual hasta ayer
  %(prog)s --year 2024 --month 12    # Extraer diciembre 2024 completo
  %(prog)s --current --dry-run       # Solo mostrar plan del mes actual
  %(prog)s --year 2024 --month 11 --report  # Solo mostrar reporte de noviembre
        """
    )
    
    parser.add_argument("--data-dir", 
                       help="Directorio raíz de datos (default: ./data)")
    parser.add_argument("--year", type=int,
                       help="Año del mes a extraer")
    parser.add_argument("--month", type=int,
                       help="Mes a extraer (1-12)")
    parser.add_argument("--end-day", type=int,
                       help="Último día del mes a extraer")
    parser.add_argument("--current", action="store_true",
                       help="Extraer mes actual hasta ayer")
    parser.add_argument("--dry-run", action="store_true",
                       help="Solo generar plan, no descargar")
    parser.add_argument("--report", action="store_true",
                       help="Solo mostrar reporte del mes")
    parser.add_argument("--overwrite", action="store_true",
                       help="Sobrescribir archivos existentes")
    parser.add_argument("--tz", default="America/Santiago",
                       help="Zona horaria (default: America/Santiago)")
    
    return parser.parse_args()


def main():
    """Función principal."""
    args = parse_args()
    
    # Crear extractor
    extractor = MonthlyExtractor(
        data_dir=args.data_dir,
        tz=args.tz
    )
    
    # Determinar año y mes
    if args.current:
        # Usar mes actual
        if args.report:
            today = datetime.now(extractor.tz).date()
            extractor.print_month_report(today.year, today.month)
        else:
            plan_csv, results_csv = extractor.run_current_month(
                dry_run=args.dry_run,
                overwrite=args.overwrite
            )
            
            print("\n📋 RESUMEN FINAL")
            print(f"📄 Plan: {plan_csv}")
            if results_csv:
                print(f"📊 Resultados: {results_csv}")
            else:
                print("📊 Resultados: (dry-run, no se ejecutaron descargas)")
    
    elif args.year and args.month:
        # Usar año y mes específicos
        if args.report:
            extractor.print_month_report(args.year, args.month)
        else:
            plan_csv, results_csv = extractor.run_month(
                year=args.year,
                month=args.month,
                end_day=args.end_day,
                dry_run=args.dry_run,
                overwrite=args.overwrite
            )
            
            print("\n📋 RESUMEN FINAL")
            print(f"📄 Plan: {plan_csv}")
            if results_csv:
                print(f"📊 Resultados: {results_csv}")
            else:
                print("📊 Resultados: (dry-run, no se ejecutaron descargas)")
    
    else:
        print("❌ Error: Debe especificar --current o --year y --month")
        sys.exit(1)


if __name__ == "__main__":
    main()