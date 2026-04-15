import os
import pandas as pd
from datetime import datetime, time
from typing import List, Dict
import json
from pathlib import Path
from tqdm import tqdm

def _load_config() -> dict:
    """Carga configuración desde config/config.json."""
    try:
        config_path = Path("config/config.json")
        with config_path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:  # noqa: BLE001
        print(f"Error cargando config.json: {e}")
    return {}


def _load_horarios() -> dict:
    """Carga horarios desde config/horarios.json."""
    try:
        horarios_path = Path("config/horarios.json")
        with horarios_path.open("r", encoding="utf-8") as f:
            horarios = json.load(f)
            # Convertir strings de hora a objetos time
            for key, horario in horarios.items():
                horario['hora_inicio'] = datetime.strptime(horario['hora_inicio'], "%H:%M").time()
                horario['hora_fin'] = datetime.strptime(horario['hora_fin'], "%H:%M").time()
            return horarios
    except Exception as e:
        print(f"Error cargando horarios.json: {e}")
    return {}


def _get_month_name_spanish(month_num: int) -> str:
    """Convierte número de mes (1-12) a nombre en español."""
    month_names = {
        1: "enero", 2: "febrero", 3: "marzo", 4: "abril",
        5: "mayo", 6: "junio", 7: "julio", 8: "agosto",
        9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre"
    }
    return month_names.get(month_num, str(month_num))


class DataTransformer:
    def __init__(self, base_path: str = None):
        # Load config and use raw path if base_path not provided
        self.config = _load_config()
        self.horarios = _load_horarios()
        if base_path:
            # Normalize incoming base path so mixed backslashes/forward-slashes
            # don't produce paths like "...\\Data_MTSolutions/EXT_15/...".
            # This replaces Windows backslashes with the current OS separator
            # and collapses redundant separators. Note: if you run inside Docker
            # you must still pass a path that exists inside the container (see
            # notes below) — this only normalizes separators.
            self.base_path = os.path.normpath(base_path.replace('\\', os.sep))
        else:
            # Use raw path from config
            raw_path = self.config.get("paths", {}).get("raw", "data/raw")
            self.base_path = os.path.normpath(raw_path.replace('\\', os.sep))
        
        # Get other paths from config
        paths = self.config.get("paths", {})
        self.processed_path = os.path.normpath(paths.get("processed", "data/processed").replace('\\', os.sep))
        self.consolidated_path = os.path.normpath(paths.get("consolidated", "data/consolidated").replace('\\', os.sep))
        self.column_mappings = {
            'product': 'Producto',
            'code_description': 'Desc_detencion',
            'duration': 'Duracion(min)',
            'order': 'OF',
            'sku': 'Sku',
            'start': 'Fecha_Hora_Inicio',
            'end': 'Fecha_Hora_Termino',
            'comments': 'Comentarios',
            'production': 'Q_prod_total',
            'plant': 'Planta',
            'format': 'Tipo_Unidad',
            'user': 'Usuario',
            'device': 'Maquina',
            'line': 'Linea',
            'turn': 'Turno',
            'code_u': 'Cod_detencion_1',
            'code_f': 'Cod_detencion_2',
            'code_t': 'Cod_detencion_3',
            'code_l': 'Cod_detencion_4',
            'code_m': 'Cod_detencion_5',
            'code_n': 'Cod_detencion_6'
        }
        self.columns_to_drop = ['end_utc', 'start_utc']
        self.column_order = ['Año', 'Mes', 'Fecha_Hora_Inicio', 'Fecha_Hora_Termino', 'Planta',
                           'Linea', 'Maquina', 'Turno', 'OF', 'Usuario', 'Producto', 'Sku', 'Tipo_Unidad',
                           'Q_prod_total', 'Duracion(min)', 'Cod_detencion_1', 'Cod_detencion_2',
                           'Cod_detencion_3', 'Cod_detencion_4', 'Cod_detencion_5', 'Cod_detencion_6',
                           'Desc_detencion', 'Comentarios']

    def read_line_data(self, line_name: str, year: str, month: str) -> pd.DataFrame:
        """Lee los datos de una línea específica para un mes y año."""
        # Build and normalize path to avoid mixed separators (\ and /)
        # Use year/month structure as implemented in extractor_mensual
        path = os.path.normpath(os.path.join(self.base_path, year, f"{int(month):02d}"))

        if not os.path.exists(path):
            return None

        # Filter CSV files for this specific line (matching extractor_mensual naming pattern)
        month_name = _get_month_name_spanish(int(month))
        line_pattern = f"produccion_{year}_{month_name}_{line_name.lower()}"
        csv_files = [f for f in os.listdir(path) if f.endswith('.csv') and line_pattern in f.lower()]
        if not csv_files:
            return None

        dfs = []
        for file in csv_files:
            file_path = os.path.join(path, file)
            try:
                # Leer CSV
                df = pd.read_csv(file_path, 
                               sep=";",
                               encoding='utf-8',
                               na_values=["", "null", "NA", "N/A", "NONA"],
                               dtype={
                                   'product': str,
                                   'code_description': str,
                                   'sku': str,
                                   'comments': str,
                                   'plant': str,
                                   'format': str,
                                   'user': str,
                                   'device': str,
                                   'line': str,
                                   'turn': str,
                                   'order': str,
                                   'code_u': str,
                                   'code_f': str,
                                   'code_t': str,
                                   'code_l': str,
                                   'code_m': str,
                                   'code_n': str
                               })
                dfs.append(df)
            except Exception as e:
                continue

        return pd.concat(dfs, ignore_index=True) if dfs else None

    def transform_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """Aplica las transformaciones al DataFrame."""
        if df is None:
            return None

        # Renombrar columnas
        df = df.rename(columns=self.column_mappings)

        # Eliminar columnas no deseadas
        df = df.drop(columns=[col for col in self.columns_to_drop if col in df.columns])

        # Limpiar columna OF: convertir valores como "1234.0" o "1234,0" a entero
        if 'OF' in df.columns:
            df['OF'] = (
                df['OF']
                .astype(str)
                .str.replace(',', '.', regex=False)
                .where(df['OF'].notna(), other=pd.NA)
            )
            mask_of = df['OF'].notna()
            df.loc[mask_of, 'OF'] = pd.to_numeric(df.loc[mask_of, 'OF'], errors='coerce').astype('Int64').astype(str)
            df['OF'] = df['OF'].replace('<NA>', pd.NA)

        # Si hay código de detención, la producción debe ser 0
        df.loc[df['Desc_detencion'].notna(), 'Q_prod_total'] = 0

        # Normalizar "Período no laboral" en cod_detencion_2
        periodo_variantes = [
            'Periodo no laboral',
            'PERIODO NO LABORAL',
            'Periodo No Laboral',
            'PERÍODO NO LABORAL',
            'Periodo no Laboral',
            'periodo no laboral'
        ]

        # Normalizar los Planificado en cod_detencion_2
        planificado_variantes = [
            'Planificado',
            'Planificadas'
        ]

        #Normalizar No planificado en cod_detencion_2
        no_planificado_variantes = [
            'No planificado',
            'No Planificadas'
        ]
        # Normalizar variantes en Cod_detencion_2 
        df['Cod_detencion_2'] = df['Cod_detencion_2'].replace(
            periodo_variantes + planificado_variantes + no_planificado_variantes,
            ['Período no laboral'] * len(periodo_variantes) + 
            ['Planificado'] * len(planificado_variantes) + 
            ['No planificado'] * len(no_planificado_variantes)
        )

        # Convertir las columnas de fecha a datetime (Solución definitiva y segura)
        df['Fecha_Hora_Inicio'] = pd.to_datetime(df['Fecha_Hora_Inicio'], format='mixed', dayfirst=True)
        df['Fecha_Hora_Termino'] = pd.to_datetime(df['Fecha_Hora_Termino'], format='mixed', dayfirst=True)
        
        # Dividir registros que cruzan medianoche<
        df = self._split_midnight_crossings(df)
        
        # Extraer Año y Mes de Fecha_Hora_Inicio (después de la división para tener valores correctos)
        df['Año'] = df['Fecha_Hora_Inicio'].dt.year
        df['Mes'] = df['Fecha_Hora_Inicio'].dt.month
        
        # Crear máscaras para los criterios
        es_termoformado = df['Linea'].str.lower().str.strip() == 'termoformado'
        es_no_asignado = df['Cod_detencion_3'].str.lower().str.strip() == 'no asignado'
        
        # Extraer día de la semana (0=Lunes, 6=Domingo) y hora
        df['dia_semana'] = df['Fecha_Hora_Inicio'].dt.dayofweek
        df['hora'] = df['Fecha_Hora_Inicio'].dt.hour
        
        # Máscara para días de semana (18-22hrs)
        es_semana = (df['dia_semana'].between(0, 4)) & \
                    (df['hora'].between(18, 21))
        
        # Máscara para fin de semana (sábado 8am a lunes 8am)
        es_finde = ((df['dia_semana'] == 5) & (df['hora'] >= 8)) | \
                   (df['dia_semana'] == 6) | \
                   ((df['dia_semana'] == 0) & (df['hora'] < 8))
        
        # Aplicar la lógica completa
        mask_turno_no_usado = es_termoformado & es_no_asignado & (es_semana | es_finde)
        df.loc[mask_turno_no_usado & es_finde, 'Cod_detencion_3'] = 'Fines de Semana'
        df.loc[mask_turno_no_usado & es_semana, 'Cod_detencion_3'] = 'Turno No Usado'
        
        # Eliminar columnas auxiliares
        df = df.drop(['dia_semana', 'hora'], axis=1)

        # Calcular minutos extras para Termoformado
        df['Minutos_Extras'] = df.apply(self._calcular_minutos_extras, axis=1)
        
        # Agregar hora de término de cada turno
        df['Hora_Termino_Turno'] = df.apply(self._obtener_hora_termino_turno, axis=1)
        
        # Agregar columna de periodo laboral
        df['Periodo_Laboral'] = df.apply(self._determinar_periodo_laboral, axis=1)

        # Identificar registros "No producido"
        # Condiciones: Producto = "Sin Orden / Sin Actividad", SKU y Tipo_Unidad = "NONA"
        # Y cod_detencion_2, cod_detencion_3 y desc_detencion están vacías o null
        mask_sin_orden = (
            (df['Producto'].str.strip() == 'Sin Orden / Sin Actividad') &
            (df['Sku'].str.strip() == 'NONA') &
            (df['Tipo_Unidad'].str.strip() == 'NONA') &
            (
                (df['Cod_detencion_2'].isna() | (df['Cod_detencion_2'].str.strip() == '')) &
                (df['Cod_detencion_3'].isna() | (df['Cod_detencion_3'].str.strip() == '')) &
                (df['Desc_detencion'].isna() | (df['Desc_detencion'].str.strip() == ''))
            )
        )
        
        # Crear columna tipo_registro_1 con clasificación unificada
        codigos_planificados = ['Planificado', 'Período no laboral', 'Externa']
        
        def clasificar_tipo_registro(row):
            # Primero verificar si es "No producido"
            if mask_sin_orden[row.name]:
                return 'No producido'
            
            # Luego aplicar la lógica original
            cod_detencion_2 = row['Cod_detencion_2']
            if pd.notna(cod_detencion_2):
                if cod_detencion_2 in codigos_planificados:
                    return 'Planificado'
                elif cod_detencion_2.strip() != '':
                    return 'No planificado'
            return 'Producción'
        
        # Aplicar la clasificación
        df['tipo_registro_1'] = df.apply(clasificar_tipo_registro, axis=1)

        # Agregar columna de tipo de registro (producción o detención)
        df['Tipo_registro'] = 'produccion'
        mask = (df['Cod_detencion_1'].notna() & df['Cod_detencion_1'].str.strip().ne('')) | \
               (df['Desc_detencion'].notna() & df['Desc_detencion'].str.strip().ne(''))
        df.loc[mask, 'Tipo_registro'] = 'detencion'

        # Reordenar columnas (solo las que existen en el DataFrame)
        valid_columns = [col for col in self.column_order if col in df.columns]
        remaining_columns = [col for col in df.columns if col not in valid_columns and col != 'Tipo_registro']
        df = df[valid_columns + ['Tipo_registro'] + remaining_columns]

        return df
    
    def _calcular_minutos_extras(self, row) -> float:
        """Calcula los minutos extras trabajados después del horario de término.
        Solo aplica para línea TERMOFORMADO con Q_prod_total > 0.
        """
        minutos_extras = 0
        linea = row['Linea']
        
        # Solo aplicar la lógica a la línea TERMOFORMADO
        if linea.upper().strip() != 'TERMOFORMADO':
            return minutos_extras
        
        # Solo aplicar si hay producción (Q_prod_total > 0)
        if pd.isna(row['Q_prod_total']) or row['Q_prod_total'] <= 0:
            return minutos_extras
        
        inicio = row['Fecha_Hora_Inicio']
        termino = row['Fecha_Hora_Termino']
        turno_actual = row['Turno']
        
        # Seleccionar el horario según el turno de la fila
        if turno_actual == 'TD':
            horario = self.horarios.get('Termoformado_dia')
        elif turno_actual == 'TN':
            horario = self.horarios.get('Termoformado_noche')
        else:
            return minutos_extras
        
        if not horario:
            return minutos_extras
        
        hora_inicio = datetime.combine(inicio.date(), horario['hora_inicio'])
        hora_fin = datetime.combine(inicio.date(), horario['hora_fin'])
        
        # Ajustar para turnos nocturnos
        if hora_fin <= hora_inicio:
            hora_fin += pd.Timedelta(days=1)
        
        # Calcular minutos extras según la lógica especificada
        if inicio >= hora_fin:
            # Si el inicio es mayor o igual a hora_fin: Fecha_Hora_Termino - Fecha_Hora_Inicio
            minutos_extras = (termino - inicio).total_seconds() / 60
        elif termino > hora_fin:
            # Si el inicio es menor a hora_fin: Fecha_Hora_Termino - hora_fin
            minutos_extras = (termino - hora_fin).total_seconds() / 60
        
        return minutos_extras
    
    def _obtener_hora_termino_turno(self, row):
        """Obtiene la hora de término del turno según la línea y turno.
        Devuelve la hora de término como string (texto) en formato HH:MM o None.
        """
        linea = row['Linea']
        turno_actual = row['Turno']
        
        # Solo aplicar para TERMOFORMADO
        if linea.upper().strip() != 'TERMOFORMADO':
            return None
        
        # Seleccionar el horario según el turno
        if turno_actual == 'TD':
            horario = self.horarios.get('Termoformado_dia')
        elif turno_actual == 'TN':
            horario = self.horarios.get('Termoformado_noche')
        else:
            return None
        
        if not horario:
            return None
        
        # Devolver la hora de término como string
        hora_fin = horario['hora_fin']
        return hora_fin.strftime("%H:%M")
    
    def _determinar_periodo_laboral(self, row):
        """Determina si el registro está en periodo laboral.
        Periodo laboral: dentro de las horas del horario y excluyendo domingos.
        Solo aplica para línea TERMOFORMADO.
        """
        linea = row['Linea']
        
        # Solo aplicar para TERMOFORMADO
        if linea.upper().strip() != 'TERMOFORMADO':
            return None
        
        inicio = row['Fecha_Hora_Inicio']
        
        # Verificar si es domingo (6 = domingo en Python)
        if inicio.dayofweek == 6:
            return 'No'
        
        turno_actual = row['Turno']
        
        # Seleccionar el horario según el turno
        if turno_actual == 'TD':
            horario = self.horarios.get('Termoformado_dia')
        elif turno_actual == 'TN':
            horario = self.horarios.get('Termoformado_noche')
        else:
            return None
        
        if not horario:
            return None
        
        hora_inicio_turno = datetime.combine(inicio.date(), horario['hora_inicio'])
        hora_fin_turno = datetime.combine(inicio.date(), horario['hora_fin'])
        
        # Ajustar para turnos nocturnos
        if hora_fin_turno <= hora_inicio_turno:
            hora_fin_turno += pd.Timedelta(days=1)
            # Si estamos en el día siguiente y el inicio es después de medianoche
            if inicio.time() < horario['hora_inicio']:
                hora_inicio_turno -= pd.Timedelta(days=1)
        
        # Verificar si está dentro del horario laboral
        if hora_inicio_turno <= inicio <= hora_fin_turno:
            return 'Sí'
        else:
            return 'No'
    
    def _split_midnight_crossings(self, df: pd.DataFrame) -> pd.DataFrame:
        """Divide registros que cruzan medianoche en dos registros separados.
        Redistribuye la producción y duración proporcionalmente al tiempo trabajado en cada tramo.
        
        Args:
            df: DataFrame con los registros originales
            
        Returns:
            DataFrame con registros divididos donde cruzan medianoche
        """
        if df is None or df.empty:
            return df
        
        # Guardar los tipos de datos originales
        original_dtypes = df.dtypes.to_dict()
        
        # Lista para almacenar los nuevos registros
        new_records = []
        
        for idx, row in df.iterrows():
            inicio = row['Fecha_Hora_Inicio']
            fin = row['Fecha_Hora_Termino']
            
            # Verificar si cruza medianoche (diferentes fechas)
            if inicio.date() != fin.date():
                # Calcular los puntos de corte
                fin_dia_inicio = datetime.combine(inicio.date(), time(23, 59, 59))
                inicio_dia_siguiente = datetime.combine(fin.date(), time(0, 0, 0))
                
                # Calcular duraciones en minutos
                dur_total = (fin - inicio).total_seconds() / 60
                dur_A = (fin_dia_inicio - inicio).total_seconds() / 60
                dur_B = (fin - inicio_dia_siguiente).total_seconds() / 60
                
                # Producción original
                prod_original = row['Q_prod_total'] if pd.notna(row['Q_prod_total']) else 0
                
                # Calcular producción proporcional
                if dur_total > 0:
                    prod_A_raw = prod_original * (dur_A / dur_total)
                    prod_A = round(prod_A_raw, 2)  # Redondear a 2 decimales
                    prod_B = round(prod_original - prod_A, 2)  # Garantizar suma exacta
                else:
                    prod_A = 0
                    prod_B = 0
                
                # Crear registro A (mismo día hasta 23:59:59)
                record_A = row.to_dict()
                record_A['Fecha_Hora_Termino'] = fin_dia_inicio
                record_A['Duracion(min)'] = round(dur_A, 2)
                record_A['Q_prod_total'] = prod_A
                record_A['Registro_Dividido'] = 'Parte_1'
                new_records.append(record_A)
                
                # Crear registro B (día siguiente desde 00:00:00)
                record_B = row.to_dict()
                record_B['Fecha_Hora_Inicio'] = inicio_dia_siguiente
                record_B['Fecha_Hora_Termino'] = fin
                record_B['Duracion(min)'] = round(dur_B, 2)
                record_B['Q_prod_total'] = prod_B
                record_B['Registro_Dividido'] = 'Parte_2'
                new_records.append(record_B)
                
            else:
                # No cruza medianoche, mantener registro original
                record = row.to_dict()
                record['Registro_Dividido'] = 'No'
                new_records.append(record)
        
        # Crear nuevo DataFrame con los registros procesados
        result_df = pd.DataFrame(new_records)
        
        # Restaurar tipos de datos para columnas string
        string_columns = ['Producto', 'Desc_detencion', 'Sku', 'Comentarios', 'Planta', 
                          'Tipo_Unidad', 'Usuario', 'Maquina', 'Linea', 'Turno',
                          'Cod_detencion_1', 'Cod_detencion_2', 'Cod_detencion_3', 
                          'Cod_detencion_4', 'Cod_detencion_5', 'Cod_detencion_6']
        
        for col in string_columns:
            if col in result_df.columns:
                result_df[col] = result_df[col].astype(str).replace('nan', pd.NA)
        
        # Resetear índice
        result_df = result_df.reset_index(drop=True)
        
        return result_df
    
    def save_processed_data(self, df: pd.DataFrame, line_name: str, year: str, month: str) -> str:
        """Guarda los datos procesados con la estructura año/mes y nuevo formato de nombre."""
        if df is None or df.empty:
            return None

        # Crear estructura de directorios año/mes
        output_dir = os.path.join(self.processed_path, year, f"{int(month):02d}")
        os.makedirs(output_dir, exist_ok=True)
        
        # Nuevo formato de nombre: processed_prod_{año}_{mes}_{linea}
        month_name = _get_month_name_spanish(int(month))
        filename = f"processed_prod_{year}_{month_name}_{line_name.lower()}.csv"
        
        output_path = os.path.join(output_dir, filename)
        
        # Guardar archivo con formato de fecha seguro para Power BI
        df.to_csv(output_path, sep=";", index=False, encoding='utf-8-sig', decimal=',', date_format='%Y-%m-%d %H:%M:%S')
        return output_path
    
    def process_line(self, line_name: str, year: str, pbar: tqdm = None) -> pd.DataFrame:
        """Procesa todos los meses de una línea para un año específico y guarda archivos procesados."""
        processed_files = []
        
        for month in [f"{m:02d}" for m in range(1, 13)]:
            if pbar:
                pbar.set_description(f"Procesando {line_name}-{month}")
            
            monthly_df = self.read_line_data(line_name, year, month)
            
            if monthly_df is not None:
                # Aplicar transformaciones (incluye extracción de Año y Mes de Fecha_Hora_Inicio)
                monthly_df = self.transform_data(monthly_df)
                
                # Guardar datos procesados
                processed_file = self.save_processed_data(monthly_df, line_name, year, month)
                if processed_file:
                    processed_files.append(processed_file)
            
            if pbar:
                pbar.update(1)

        return processed_files
    
    def consolidate_processed_data(self, years: list = None) -> str:
        """Lee todos los archivos procesados de todos los años y los consolida en un único archivo."""
        # Si no se especifican años, detectar automáticamente
        if years is None:
            years = []
            if os.path.exists(self.processed_path):
                for item in os.listdir(self.processed_path):
                    item_path = os.path.join(self.processed_path, item)
                    if os.path.isdir(item_path) and item.isdigit() and len(item) == 4:
                        years.append(item)
            years.sort()
        
        if not years:
            return None
        
        # Buscar todos los archivos procesados de todos los años
        all_data = []
        
        print("\nConsolidando archivos...")
        for year in years:
            processed_base = os.path.join(self.processed_path, year)
            
            if not os.path.exists(processed_base):
                continue
                
            # Recorrer todos los meses del año
            for month in [f"{m:02d}" for m in range(1, 13)]:
                month_dir = os.path.join(processed_base, month)
                if not os.path.exists(month_dir):
                    continue
                    
                # Buscar archivos procesados en este mes
                processed_files = [f for f in os.listdir(month_dir) 
                                 if f.startswith('processed_prod_') and f.endswith('.csv')]
                
                for file in processed_files:
                    file_path = os.path.join(month_dir, file)
                    try:
                        df = pd.read_csv(file_path, 
                                       sep=";",
                                       encoding='utf-8',
                                       na_values=["", "null", "NA", "N/A"])
                        if not df.empty:
                            all_data.append(df)
                    except Exception as e:
                        continue
                        
        if not all_data:
            return None
            
        # Consolidar todos los datos
        final_df = pd.concat(all_data, ignore_index=True)
        
        # Crear directorio consolidado
        os.makedirs(self.consolidated_path, exist_ok=True)
        
        # Guardar archivo consolidado único con formato de fecha seguro para Power BI
        output_filename = "mt_typ_consolidated.csv"
        output_path = os.path.join(self.consolidated_path, output_filename)
        final_df.to_csv(output_path, sep=";", index=False, encoding='utf-8-sig', decimal=',', date_format='%Y-%m-%d %H:%M:%S')
        
        print(f"\n Consolidación completa: {len(final_df):,} registros")
        print(f" Archivo: {output_path}")
        return output_path

def main():
    # Configuración
    lines = ["TF_2","TF_3","TF_5","TF_6","TF_7","TF_8","TF_9","TF_10","EXT_14","EXT_15","EXT_16"]
    
    # Crear instancia del transformador (usa config.json automáticamente)
    transformer = DataTransformer()
    
    # Detectar todos los años disponibles en el directorio raw
    available_years = []
    if os.path.exists(transformer.base_path):
        for item in os.listdir(transformer.base_path):
            item_path = os.path.join(transformer.base_path, item)
            # Verificar si es directorio y si parece un año (4 dígitos)
            if os.path.isdir(item_path) and item.isdigit() and len(item) == 4:
                available_years.append(item)
    
    available_years.sort()  # Ordenar años cronológicamente
    
    if not available_years:
        print("❌ No se encontraron años disponibles en el directorio raw")
        return
    
    print(f"Años disponibles: {', '.join(available_years)}\n")
    
    # Calcular total de operaciones para la barra de progreso
    total_operations = len(available_years) * len(lines) * 12  # años * líneas * meses
    
    # Procesar cada año con barra de progreso
    with tqdm(total=total_operations, desc="Procesando", unit="mes") as pbar:
        for year in available_years:
            pbar.write(f"\n{'='*50}")
            pbar.write(f"  AÑO {year}")
            pbar.write(f"{'='*50}")
            
            # Procesar cada línea para este año
            for line in lines:
                transformer.process_line(line, year, pbar)
    
    # Consolidar todos los archivos procesados de todos los años en un solo archivo
    print(f"\n{'='*60}")
    print(f"  CONSOLIDANDO TODOS LOS AÑOS")
    print(f"{'='*60}")
    consolidated_file = transformer.consolidate_processed_data(available_years)
    
    if consolidated_file:
        print(f"\n{'='*60}")
        print(f"PROCESO COMPLETO")
        print(f"{'='*60}")
    else:
        print(f"\n❌ No se pudo completar la consolidación")

if __name__ == "__main__":
    main()