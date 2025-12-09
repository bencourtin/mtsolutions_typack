"""
Extractor que obtiene datos en un periodo móvil de 30 días hacia atrás desde la fecha de ejecución.
Optimizado con funciones modulares reutilizables.
"""
import os
import sys
import warnings
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
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


class RollingExtractor30Days:
    """
    Extractor de ventana móvil de 30 días.
    
    Características:
    - Extrae los últimos 30 días desde ayer hacia atrás
    - Detecta archivos existentes para evitar duplicados
    - Sistema robusto de reintentos
    - Logging detallado de operaciones
    """

    def __init__(self, data_dir: Optional[str] = None, tz: str = "America/Santiago", 
                 days_window: int = 30):
        """
        Inicializa el extractor de 30 días.
        
        Args:
            data_dir: Directorio base para datos (opcional)
            tz: Zona horaria para operaciones
            days_window: Ventana de días hacia atrás (default: 30)
        """
        self.tz = ZoneInfo(tz)
        self.days_window = days_window
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

    def run(self, run_date: Optional[date] = None, dry_run: bool = False, 
            overwrite: bool = False) -> Tuple[Optional[str], Optional[str]]:
        """
        Ejecuta la extracción de 30 días móviles.
        
        Args:
            run_date: Fecha de referencia (default: hoy)
            dry_run: Solo generar plan sin descargar
            overwrite: Sobrescribir archivos existentes
            
        Returns:
            Tuple con rutas de archivos de plan y resultados
        """
        if run_date is None:
            run_date = datetime.now(self.tz).date()

        end_date = run_date - timedelta(days=1)  # Hasta ayer
        start_date = end_date - timedelta(days=self.days_window - 1)  # 30 días hacia atrás

        print(f"\n🔄 EXTRACTOR 30 DÍAS MÓVILES")
        print(f"📅 Período: {start_date} → {end_date} ({self.days_window} días)")
        print(f"📊 Líneas configuradas: {len(self.lines)}")
        
        # Generar plan de descarga
        plan = self.plan_manager.build_rolling_plan(
            lines=self.lines, 
            run_date=run_date, 
            days_back=self.days_window
        )
        
        if dry_run:
            print("🧪 Modo dry-run: solo generando plan")
            return (LogUtils.latest_log_path(self.logs_dir, "plan_rolling"), None)
        
        # Ejecutar descargas
        results_csv = self.batch_downloader.execute_plan(
            plan=plan,
            token_renew_every=25,  # Renovar token más frecuentemente
            sleep_between=0.5,     # Pausa corta entre descargas
            max_retries=3
        )
        
        # Limpiar logs antiguos
        LogUtils.cleanup_old_logs(self.logs_dir, "plan_rolling", keep_count=5)
        LogUtils.cleanup_old_logs(self.logs_dir, "results", keep_count=10)
        
        return (LogUtils.latest_log_path(self.logs_dir, "plan_rolling"), results_csv)

    def get_coverage_summary(self, run_date: Optional[date] = None) -> dict:
        """
        Retorna un resumen de cobertura de datos para el período de 30 días.
        
        Args:
            run_date: Fecha de referencia (default: hoy)
            
        Returns:
            Dict con estadísticas de cobertura por línea
        """
        if run_date is None:
            run_date = datetime.now(self.tz).date()
        
        end_date = run_date - timedelta(days=1)
        start_date = end_date - timedelta(days=self.days_window - 1)
        
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
            
            coverage_pct = (len(existing_dates) / self.days_window) * 100
            
            summary[line_name] = {
                "total_days": self.days_window,
                "existing_files": len(existing_dates),
                "missing_files": len(missing_dates),
                "coverage_percent": round(coverage_pct, 1),
                "period_start": start_date,
                "period_end": end_date
            }
        
        return summary

    def print_coverage_report(self, run_date: Optional[date] = None):
        """Imprime un reporte de cobertura en consola."""
        summary = self.get_coverage_summary(run_date)
        
        print(f"\n📊 REPORTE DE COBERTURA - {self.days_window} DÍAS")
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
        description="Extractor de 30 días móviles para TYPACK ETL",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos de uso:
  %(prog)s                           # Extraer últimos 30 días
  %(prog)s --days 7                  # Extraer últimos 7 días  
  %(prog)s --dry-run                 # Solo mostrar plan
  %(prog)s --coverage                # Solo mostrar cobertura
  %(prog)s --run-date 2024-01-15     # Usar fecha específica
        """
    )
    
    parser.add_argument("--data-dir", 
                       help="Directorio raíz de datos (default: ./data)")
    parser.add_argument("--run-date", 
                       help="Fecha de referencia YYYY-MM-DD (default: hoy)")
    parser.add_argument("--days", type=int, default=30,
                       help="Días hacia atrás a extraer (default: 30)")
    parser.add_argument("--dry-run", action="store_true",
                       help="Solo generar plan, no descargar")
    parser.add_argument("--coverage", action="store_true",
                       help="Solo mostrar reporte de cobertura")
    parser.add_argument("--overwrite", action="store_true",
                       help="Sobrescribir archivos existentes")
    parser.add_argument("--tz", default="America/Santiago",
                       help="Zona horaria (default: America/Santiago)")
    
    return parser.parse_args()


def main():
    """Función principal."""
    args = parse_args()
    
    # Parsear fecha si se proporciona
    run_date = None
    if args.run_date:
        try:
            run_date = datetime.strptime(args.run_date, "%Y-%m-%d").date()
        except ValueError:
            print("❌ Error: Formato de fecha inválido. Use YYYY-MM-DD")
            sys.exit(1)
    
    # Crear extractor
    extractor = RollingExtractor30Days(
        data_dir=args.data_dir,
        tz=args.tz,
        days_window=args.days
    )
    
    # Ejecutar según modo
    if args.coverage:
        extractor.print_coverage_report(run_date)
    else:
        plan_csv, results_csv = extractor.run(
            run_date=run_date,
            dry_run=args.dry_run,
            overwrite=args.overwrite
        )
        
        print("\n📋 RESUMEN FINAL")
        print(f"📄 Plan: {plan_csv}")
        if results_csv:
            print(f"📊 Resultados: {results_csv}")
        else:
            print("📊 Resultados: (dry-run, no se ejecutaron descargas)")


if __name__ == "__main__":
    main()

