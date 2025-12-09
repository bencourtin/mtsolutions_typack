"""
Escaner de fechas para detectar archivos existentes
"""
import os
import re
from datetime import date, datetime
from typing import Optional


class DateScanner:
    """Escanea archivos para encontrar fechas disponibles."""
    
    FILE_REGEX = re.compile(r"produccion_(\d{4}-\d{2}-\d{2})_")
    
    def __init__(self, path_manager):
        self.path_manager = path_manager
    
    def parse_date_from_filename(self, filename: str) -> Optional[date]:
        """Extrae la fecha de un nombre de archivo usando regex."""
        match = self.FILE_REGEX.search(filename)
        if not match:
            return None
        try:
            return datetime.strptime(match.group(1), "%Y-%m-%d").date()
        except ValueError:
            return None
    
    def last_date_for_line(self, line_name: str) -> Optional[date]:
        """
        Busca la última fecha disponible para una línea.
        Retorna None si no hay archivos.
        """
        root_dir = self.path_manager.line_output_root(line_name)
        if not os.path.isdir(root_dir):
            return None
        
        latest_date = None
        
        try:
            # Recorrer años
            for year in os.listdir(root_dir):
                year_path = os.path.join(root_dir, year)
                if not (year.isdigit() and os.path.isdir(year_path)):
                    continue
                
                # Recorrer meses
                for month in os.listdir(year_path):
                    month_path = os.path.join(year_path, month)
                    if not (month.isdigit() and os.path.isdir(month_path)):
                        continue
                    
                    # Recorrer archivos CSV
                    for filename in os.listdir(month_path):
                        if not filename.endswith(".csv"):
                            continue
                        
                        file_date = self.parse_date_from_filename(filename)
                        if file_date and (latest_date is None or file_date > latest_date):
                            latest_date = file_date
        
        except (OSError, PermissionError) as e:
            print(f"[WARN] Error escaneando {root_dir}: {e}")
            return None
        
        return latest_date
    
    def has_data_for_date(self, line_name: str, target_date: date) -> bool:
        """Verifica si ya existe un archivo para una línea y fecha específica."""
        out_dir = self.path_manager.out_dir_for(line_name, target_date)
        filename = self.path_manager.filename_for(line_name, target_date)
        file_path = os.path.join(out_dir, filename)
        return os.path.exists(file_path) and os.path.getsize(file_path) > 0