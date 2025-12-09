"""
Gestor de planes de descarga
"""
import os
from datetime import date, timedelta, datetime
from typing import List, Dict, Optional
import polars as pl


class PlanManager:
    """Genera y gestiona planes de descarga."""
    
    def __init__(self, path_manager, date_scanner, logs_dir: str, timezone):
        self.path_manager = path_manager
        self.date_scanner = date_scanner
        self.logs_dir = logs_dir
        self.tz = timezone
    
    def build_incremental_plan(self, lines: List[Dict], run_date: date, 
                             first_date_if_empty: Optional[date] = None) -> List[Dict]:
        """
        Genera plan incremental desde la última fecha hasta run_date-1.
        """
        target_end = run_date - timedelta(days=1)
        plan = []
        
        for line in lines:
            line_name = line["name"]
            last_date = self.date_scanner.last_date_for_line(line_name)
            
            if last_date is None:
                start_date = first_date_if_empty or target_end
            else:
                start_date = last_date + timedelta(days=1)
            
            if start_date > target_end:
                continue  # Nada pendiente
            
            # Agregar fechas al plan
            current_date = start_date
            while current_date <= target_end:
                plan.append(self._create_plan_item(line, current_date))
                current_date += timedelta(days=1)
        
        return self._save_plan(plan, "incremental")
    
    def build_rolling_plan(self, lines: List[Dict], run_date: date, days_back: int = 30) -> List[Dict]:
        """
        Genera plan de ventana móvil de N días hacia atrás desde run_date-1.
        """
        end_date = run_date - timedelta(days=1)
        start_date = end_date - timedelta(days=days_back-1)
        plan = []
        
        for line in lines:
            current_date = start_date
            while current_date <= end_date:
                # Solo agregar si no existe o está vacío
                if not self.date_scanner.has_data_for_date(line["name"], current_date):
                    plan.append(self._create_plan_item(line, current_date))
                current_date += timedelta(days=1)
        
        return self._save_plan(plan, f"rolling_{days_back}d")
    
    def build_date_range_plan(self, lines: List[Dict], start_date: date, 
                            end_date: date, overwrite: bool = False) -> List[Dict]:
        """
        Genera plan para un rango específico de fechas.
        """
        plan = []
        
        for line in lines:
            current_date = start_date
            while current_date <= end_date:
                # Agregar si no existe o si se permite sobrescribir
                if overwrite or not self.date_scanner.has_data_for_date(line["name"], current_date):
                    plan.append(self._create_plan_item(line, current_date))
                current_date += timedelta(days=1)
        
        return self._save_plan(plan, f"range_{start_date}_{end_date}")
    
    def _create_plan_item(self, line: Dict, target_date: date) -> Dict:
        """Crea un item del plan con toda la información necesaria."""
        out_dir = self.path_manager.out_dir_for(line["name"], target_date)
        out_path = os.path.join(out_dir, self.path_manager.filename_for(line["name"], target_date))
        
        return {
            "line_name": line["name"],
            "devid": line.get("devid"),
            "date": target_date,
            "out_dir": out_dir,
            "out_path": out_path
        }
    
    def _save_plan(self, plan: List[Dict], plan_type: str) -> List[Dict]:
        """Guarda el plan en un archivo CSV y retorna el plan."""
        timestamp = datetime.now(self.tz).strftime("%Y%m%d_%H%M%S")
        plan_csv = os.path.join(self.logs_dir, f"plan_{plan_type}_{timestamp}.csv")
        
        if plan:
            df_plan = pl.DataFrame([{
                "line": item["line_name"],
                "devid": item["devid"],
                "date": item["date"].strftime("%Y-%m-%d"),
                "output_path": item["out_path"]
            } for item in plan])
            df_plan.write_csv(plan_csv, separator=";")
            print(f"[INFO] Plan {plan_type} guardado: {len(plan)} descargas en {plan_csv}")
        else:
            # Crear archivo vacío para registro
            with open(plan_csv, "w", encoding="utf-8") as f:
                f.write("line;devid;date;output_path\n")
            print(f"[INFO] Plan {plan_type} vacío guardado en {plan_csv}")
        
        return plan