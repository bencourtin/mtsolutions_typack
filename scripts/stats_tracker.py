"""
Módulo para tracking de estadísticas de ejecución del ETL.

Guarda un registro de cada ejecución y permite generar reportes semanales
con métricas agregadas.

Los datos se almacenan en JSON dentro del directorio de logs configurado.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from config.settings import PATHS


class StatsTracker:
    """Clase para registrar y consultar estadísticas de ejecución del ETL."""
    
    def __init__(self):
        logs_path = PATHS.get("logs", "logs")
        self.stats_dir = Path(logs_path) / "stats"
        self.stats_dir.mkdir(parents=True, exist_ok=True)
        self.stats_file = self.stats_dir / "execution_stats.json"
        self.lines_file = self.stats_dir / "lines_without_data.json"
        
    def _load_stats(self) -> List[Dict]:
        """Carga estadísticas existentes."""
        if self.stats_file.exists():
            try:
                with self.stats_file.open("r", encoding="utf-8") as f:
                    return json.load(f)
            except:
                pass
        return []
    
    def _save_stats(self, stats: List[Dict]):
        """Guarda estadísticas."""
        with self.stats_file.open("w", encoding="utf-8") as f:
            json.dump(stats, f, indent=2, ensure_ascii=False, default=str)
    
    def _load_lines_data(self) -> Dict:
        """Carga registro de líneas sin datos."""
        if self.lines_file.exists():
            try:
                with self.lines_file.open("r", encoding="utf-8") as f:
                    return json.load(f)
            except:
                pass
        return {"lines_without_data": {}}
    
    def _save_lines_data(self, data: Dict):
        """Guarda registro de líneas sin datos."""
        with self.lines_file.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    
    def record_execution(
        self,
        success: bool,
        rows_extracted: int = 0,
        rows_transformed: int = 0,
        duration_seconds: float = 0,
        errors: List[str] = None,
        lines_without_data: List[str] = None
    ):
        """
        Registra una ejecución del ETL.
        
        Args:
            success: Si la ejecución fue exitosa
            rows_extracted: Número de filas extraídas
            rows_transformed: Número de filas transformadas
            duration_seconds: Duración en segundos
            errors: Lista de errores ocurridos
            lines_without_data: Lista de líneas que no tuvieron datos
        """
        execution_record = {
            "timestamp": datetime.now().isoformat(),
            "date": datetime.now().strftime("%Y-%m-%d"),
            "success": success,
            "rows_extracted": rows_extracted,
            "rows_transformed": rows_transformed,
            "duration_seconds": duration_seconds,
            "errors": errors or []
        }
        
        # Cargar y actualizar estadísticas
        stats = self._load_stats()
        stats.append(execution_record)
        
        # Mantener solo los últimos 90 días
        cutoff_date = (datetime.now() - timedelta(days=90)).isoformat()
        stats = [s for s in stats if s.get("timestamp", "") > cutoff_date]
        
        self._save_stats(stats)
        
        # Registrar líneas sin datos
        if lines_without_data:
            self._record_lines_without_data(lines_without_data)
    
    def _record_lines_without_data(self, lines: List[str]):
        """Registra líneas que no tuvieron datos en la ejecución actual."""
        data = self._load_lines_data()
        today = datetime.now().strftime("%Y-%m-%d")
        
        for line in lines:
            if line not in data["lines_without_data"]:
                data["lines_without_data"][line] = []
            
            # Evitar duplicados del mismo día
            if today not in data["lines_without_data"][line]:
                data["lines_without_data"][line].append(today)
            
            # Mantener solo últimos 30 días
            cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
            data["lines_without_data"][line] = [
                d for d in data["lines_without_data"][line] if d > cutoff
            ]
        
        self._save_lines_data(data)
    
    def record_line_without_data(self, line_name: str):
        """Registra una línea específica que no tuvo datos."""
        self._record_lines_without_data([line_name])
    
    def get_weekly_stats(self) -> Optional[Dict]:
        """
        Obtiene estadísticas de la última semana.
        
        Returns:
            Diccionario con estadísticas semanales o None si no hay datos
        """
        stats = self._load_stats()
        
        if not stats:
            return None
        
        # Calcular rango de la semana (últimos 7 días)
        today = datetime.now()
        week_end = today
        week_start = today - timedelta(days=7)
        
        # Filtrar ejecuciones de la semana
        week_stats = [
            s for s in stats
            if s.get("date", "") >= week_start.strftime("%Y-%m-%d")
        ]
        
        if not week_stats:
            return None
        
        # Calcular métricas
        total_executions = len(week_stats)
        successful_executions = sum(1 for s in week_stats if s.get("success", False))
        failed_executions = total_executions - successful_executions
        
        total_rows_extracted = sum(s.get("rows_extracted", 0) for s in week_stats)
        total_rows_transformed = sum(s.get("rows_transformed", 0) for s in week_stats)
        
        # Recolectar errores únicos
        all_errors = []
        for s in week_stats:
            all_errors.extend(s.get("errors", []))
        
        # Obtener líneas sin datos de la semana
        lines_data = self._load_lines_data()
        lines_without_data = []
        
        for line, dates in lines_data.get("lines_without_data", {}).items():
            week_dates = [
                d for d in dates
                if d >= week_start.strftime("%Y-%m-%d")
            ]
            if week_dates:
                lines_without_data.append({
                    "line": line,
                    "days_count": len(week_dates),
                    "dates": week_dates
                })
        
        # Ordenar por número de días sin datos (mayor primero)
        lines_without_data.sort(key=lambda x: x["days_count"], reverse=True)
        
        return {
            "week_start": week_start.strftime("%Y-%m-%d"),
            "week_end": week_end.strftime("%Y-%m-%d"),
            "total_executions": total_executions,
            "successful_executions": successful_executions,
            "failed_executions": failed_executions,
            "total_rows_extracted": total_rows_extracted,
            "total_rows_transformed": total_rows_transformed,
            "lines_without_data": lines_without_data,
            "errors": list(set(all_errors))[:20],  # Únicos, máximo 20
            "execution_dates": sorted(set(s.get("date", "") for s in week_stats))
        }
    
    def get_daily_summary(self, date: str = None) -> Optional[Dict]:
        """
        Obtiene resumen de un día específico.
        
        Args:
            date: Fecha en formato YYYY-MM-DD (default: hoy)
        
        Returns:
            Diccionario con resumen del día o None
        """
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")
        
        stats = self._load_stats()
        day_stats = [s for s in stats if s.get("date", "") == date]
        
        if not day_stats:
            return None
        
        return {
            "date": date,
            "executions": len(day_stats),
            "success_count": sum(1 for s in day_stats if s.get("success", False)),
            "total_rows_extracted": sum(s.get("rows_extracted", 0) for s in day_stats),
            "total_rows_transformed": sum(s.get("rows_transformed", 0) for s in day_stats),
            "errors": [e for s in day_stats for e in s.get("errors", [])]
        }
    
    def cleanup_old_data(self, days_to_keep: int = 90):
        """Limpia datos antiguos del tracker."""
        cutoff_date = (datetime.now() - timedelta(days=days_to_keep)).isoformat()
        
        # Limpiar estadísticas de ejecución
        stats = self._load_stats()
        stats = [s for s in stats if s.get("timestamp", "") > cutoff_date]
        self._save_stats(stats)
        
        # Limpiar registros de líneas sin datos
        cutoff_str = (datetime.now() - timedelta(days=days_to_keep)).strftime("%Y-%m-%d")
        lines_data = self._load_lines_data()
        
        for line in list(lines_data.get("lines_without_data", {}).keys()):
            lines_data["lines_without_data"][line] = [
                d for d in lines_data["lines_without_data"][line]
                if d > cutoff_str
            ]
            # Eliminar entradas vacías
            if not lines_data["lines_without_data"][line]:
                del lines_data["lines_without_data"][line]
        
        self._save_lines_data(lines_data)


if __name__ == "__main__":
    # Test básico
    tracker = StatsTracker()
    
    # Simular una ejecución
    tracker.record_execution(
        success=True,
        rows_extracted=1500,
        rows_transformed=1450,
        duration_seconds=120,
        lines_without_data=["TF_10"]
    )
    
    # Obtener estadísticas
    weekly = tracker.get_weekly_stats()
    if weekly:
        print("Estadísticas semanales:")
        print(json.dumps(weekly, indent=2, ensure_ascii=False))
    else:
        print("No hay estadísticas disponibles.")