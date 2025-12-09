"""
Utilidades para manejo de logs
"""
import os
from typing import Optional


class LogUtils:
    """Utilidades para manejo de archivos de log."""
    
    @staticmethod
    def latest_log_path(logs_dir: str, prefix: str) -> Optional[str]:
        """Encuentra el archivo de log más reciente con un prefijo específico."""
        try:
            files = [f for f in os.listdir(logs_dir) if f.startswith(prefix) and f.endswith('.csv')]
            if not files:
                return None
            
            files.sort(reverse=True)  # Más reciente primero
            return os.path.join(logs_dir, files[0])
        
        except (OSError, PermissionError):
            return None
    
    @staticmethod
    def ensure_logs_dir(logs_dir: str) -> str:
        """Asegura que el directorio de logs exista."""
        os.makedirs(logs_dir, exist_ok=True)
        return logs_dir
    
    @staticmethod
    def cleanup_old_logs(logs_dir: str, prefix: str, keep_count: int = 10):
        """Mantiene solo los N logs más recientes con un prefijo específico."""
        try:
            files = [f for f in os.listdir(logs_dir) if f.startswith(prefix) and f.endswith('.csv')]
            if len(files) <= keep_count:
                return
            
            files.sort(reverse=True)  # Más reciente primero
            files_to_delete = files[keep_count:]
            
            for file in files_to_delete:
                file_path = os.path.join(logs_dir, file)
                os.remove(file_path)
                print(f"[INFO] Log antiguo eliminado: {file}")
        
        except (OSError, PermissionError) as e:
            print(f"[WARN] Error limpiando logs: {e}")