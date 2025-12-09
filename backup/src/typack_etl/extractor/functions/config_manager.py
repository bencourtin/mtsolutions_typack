"""
Gestor de configuración para el extractor TYPACK
"""
import os
import json
from typing import Dict, List, Optional


class ConfigManager:
    """Maneja la lectura de configuraciones desde archivos JSON."""
    
    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self._config_cache = {}
    
    def get_config(self) -> Dict:
        """Lee la configuración completa desde config/config.json."""
        if 'main' in self._config_cache:
            return self._config_cache['main']
        
        # Navegar hacia arriba para encontrar el directorio config
        current_dir = self.base_dir
        for _ in range(5):  # Máximo 5 niveles hacia arriba
            config_path = os.path.join(current_dir, "config", "config.json")
            if os.path.exists(config_path):
                try:
                    with open(config_path, "r", encoding="utf-8") as f:
                        config = json.load(f)
                    self._config_cache['main'] = config
                    return config
                except Exception as e:
                    print(f"[WARN] Error leyendo {config_path}: {e}")
                    break
            current_dir = os.path.dirname(current_dir)
        
        self._config_cache['main'] = {}
        return {}
    
    def get_lines_config(self) -> List[Dict]:
        """Lee la configuración de líneas desde config/lineas.json."""
        if 'lines' in self._config_cache:
            return self._config_cache['lines']
        
        current_dir = self.base_dir
        for _ in range(5):
            config_path = os.path.join(current_dir, "config", "lineas.json")
            if os.path.exists(config_path):
                try:
                    with open(config_path, "r", encoding="utf-8") as f:
                        config = json.load(f)
                    lines = config.get("lineas", [])
                    self._config_cache['lines'] = lines
                    return lines
                except Exception as e:
                    print(f"[WARN] Error leyendo {config_path}: {e}")
                    break
            current_dir = os.path.dirname(current_dir)
        
        self._config_cache['lines'] = []
        return []
    
    def ensure_data_dir(self, custom_path: Optional[str] = None) -> str:
        """Crea y retorna el directorio de datos."""
        if custom_path:
            os.makedirs(custom_path, exist_ok=True)
            return custom_path
        
        data_dir = os.path.join(self.base_dir, "data")
        os.makedirs(data_dir, exist_ok=True)
        return data_dir


class PathManager:
    """Maneja las rutas de archivos y directorios."""
    
    def __init__(self, output_dir: str):
        self.output_dir = output_dir
    
    def line_output_root(self, line_name: str) -> str:
        """Retorna la ruta raíz para una línea."""
        return os.path.join(self.output_dir, line_name)
    
    def out_dir_for(self, line_name: str, date_obj) -> str:
        """Retorna el directorio de salida para una línea y fecha."""
        return os.path.join(
            self.line_output_root(line_name),
            f"{date_obj.year:04d}", 
            f"{date_obj.month:02d}"
        )
    
    def safe_line_name(self, name: str) -> str:
        """Sanitiza el nombre de línea para uso en nombres de archivo."""
        return "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in name)
    
    def filename_for(self, line_name: str, date_obj) -> str:
        """Genera el nombre de archivo para una línea y fecha."""
        safe_name = self.safe_line_name(line_name)
        return f"produccion_{date_obj:%Y-%m-%d}_{safe_name}.csv"