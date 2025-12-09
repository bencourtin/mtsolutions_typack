"""
Descargador de datos desde la API
"""
import os
import sys
import time
from datetime import date, datetime
from typing import Dict, List, Tuple, Optional
import polars as pl

# Ajustar path para importaciones de servicios
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

from services.auth import get_token
from services.reports import get_sabana_report


class Downloader:
    """Descarga datos de una fecha y línea específica."""
    
    def __init__(self, path_manager):
        self.path_manager = path_manager
    
    def polars_csv_from_data(self, datos: List[Dict]) -> pl.DataFrame:
        """Convierte datos JSON a DataFrame de Polars con formato específico."""
        df = pl.DataFrame(datos)
        
        # Convertir floats para usar coma decimal y vacío para None
        float_cols = [c for c, dt in zip(df.columns, df.dtypes) 
                     if dt in (pl.Float64, pl.Float32)]
        
        for col in float_cols:
            df = df.with_columns(
                pl.col(col).map_elements(
                    lambda x: "" if x is None else str(x).replace(".", ",")
                ).alias(col)
            )
        
        return df
    
    def download_single(self, line: Dict, target_date: date, 
                       token: str, quiet: bool = True) -> Tuple[bool, Optional[str]]:
        """
        Descarga datos para una línea y fecha específica.
        Retorna (success, error_message).
        """
        line_name = line["name"]
        devid = line["devid"]
        
        start_datetime = f"{target_date:%Y-%m-%d} 00:00:00"
        end_datetime = f"{target_date:%Y-%m-%d} 23:59:59"
        
        try:
            # Llamar a la API
            datos = get_sabana_report(token, start=start_datetime, end=end_datetime, devid=devid)
            
            if not datos:
                return False, "Respuesta vacía del servicio"
            
            # Convertir a DataFrame
            df = self.polars_csv_from_data(datos)
            
            # Preparar directorio de salida
            out_dir = self.path_manager.out_dir_for(line_name, target_date)
            os.makedirs(out_dir, exist_ok=True)
            
            # Guardar archivo
            out_path = os.path.join(out_dir, self.path_manager.filename_for(line_name, target_date))
            df.write_csv(out_path, separator=";")
            
            if not quiet:
                print(f"[OK] {line_name} {target_date}: {len(df)} registros")
            
            return True, None
        
        except Exception as e:
            error_msg = f"Error descargando {line_name} {target_date}: {str(e)}"
            if not quiet:
                print(f"[ERROR] {error_msg}")
            return False, error_msg


class BatchDownloader:
    """Ejecuta descargas en lote con manejo de errores y logging."""
    
    def __init__(self, downloader: Downloader, logs_dir: str, timezone):
        self.downloader = downloader
        self.logs_dir = logs_dir
        self.tz = timezone
    
    def execute_plan(self, plan: List[Dict], token_renew_every: int = 50, 
                    sleep_between: float = 1.0, max_retries: int = 3) -> str:
        """
        Ejecuta un plan de descarga con manejo robusto de errores.
        Retorna la ruta del archivo de resultados.
        """
        if not plan:
            return self._save_empty_results()
        
        print(f"[INFO] Iniciando descarga de {len(plan)} archivos...")
        
        token = get_token()
        results = []
        
        for i, item in enumerate(plan, 1):
            # Renovar token periódicamente
            if (i - 1) % token_renew_every == 0 and i > 1:
                token = get_token()
                print(f"[INFO] Token renovado en descarga {i}")
            
            # Intentar descarga con reintentos
            success, error = self._download_with_retry(item, token, max_retries)
            
            results.append({
                "line": item["line_name"],
                "date": item["date"].strftime("%Y-%m-%d"),
                "success": success,
                "error": error or ""
            })
            
            # Pausa entre descargas
            if i < len(plan):
                time.sleep(sleep_between)
            
            # Progreso cada 10 descargas
            if i % 10 == 0 or i == len(plan):
                successful = sum(1 for r in results if r["success"])
                print(f"[INFO] Progreso: {i}/{len(plan)} ({successful} exitosas)")
        
        return self._save_results(results)
    
    def _download_with_retry(self, item: Dict, token: str, max_retries: int) -> Tuple[bool, Optional[str]]:
        """Descarga con reintentos automáticos."""
        line = {"name": item["line_name"], "devid": item["devid"]}
        
        for attempt in range(max_retries):
            success, error = self.downloader.download_single(line, item["date"], token, quiet=True)
            
            if success:
                return True, None
            
            if attempt < max_retries - 1:
                wait_time = (attempt + 1) * 2  # Backoff exponencial
                print(f"[WARN] Reintento {attempt + 1}/{max_retries} para {item['line_name']} {item['date']} en {wait_time}s")
                time.sleep(wait_time)
            else:
                return False, error
        
        return False, "Máximo de reintentos alcanzado"
    
    def _save_empty_results(self) -> str:
        """Guarda archivo de resultados vacío."""
        timestamp = datetime.now(self.tz).strftime("%Y%m%d_%H%M%S")
        results_csv = os.path.join(self.logs_dir, f"results_{timestamp}.csv")
        
        with open(results_csv, "w", encoding="utf-8") as f:
            f.write("line;date;success;error\n")
        
        print("[INFO] No hay descargas pendientes.")
        return results_csv
    
    def _save_results(self, results: List[Dict]) -> str:
        """Guarda resultados de la ejecución."""
        timestamp = datetime.now(self.tz).strftime("%Y%m%d_%H%M%S")
        results_csv = os.path.join(self.logs_dir, f"results_{timestamp}.csv")
        
        pl.DataFrame(results).write_csv(results_csv, separator=";")
        
        # Resumen
        total = len(results)
        successful = sum(1 for r in results if r["success"])
        failed = total - successful
        
        print(f"[INFO] Resultados guardados en: {results_csv}")
        print(f"[INFO] Resumen: {successful}/{total} exitosas, {failed} fallos")
        
        # Mostrar errores si los hay
        failures = [r for r in results if not r["success"]]
        if failures:
            print("\n=== ERRORES DETECTADOS ===")
            for fail in failures[:10]:  # Mostrar máximo 10 errores
                print(f"  {fail['line']} {fail['date']}: {fail['error']}")
            if len(failures) > 10:
                print(f"  ... y {len(failures) - 10} errores más")
        else:
            print("✅ Todas las descargas completadas exitosamente")
        
        return results_csv