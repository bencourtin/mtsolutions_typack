import os
import pandas as pd
from datetime import datetime
from typing import List, Dict

path_folder = r"G:\Unidades compartidas\Typack Chile\Gerencia de Finanzas\Control de Gestion\Gestion Finanzas\Data_MTSolutions"
class DataTransformer:
    def __init__(self, base_path: str):
        self.base_path = base_path
        self.column_mappings = {
            'product': 'Producto',
            'code_description': 'Desc_detencion',
            'duration': 'Duracion(min)',
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
        self.columns_to_drop = ['end_utc', 'start_utc', 'order','devid']
        self.column_order = ['Año', 'Mes', 'Fecha_Hora_Inicio', 'Fecha_Hora_Termino', 'Planta',
                           'Linea', 'Maquina', 'Turno', 'Usuario', 'Producto', 'Sku', 'Tipo_Unidad',
                           'Q_prod_total', 'Duracion(min)', 'Cod_detencion_1', 'Cod_detencion_2',
                           'Cod_detencion_3', 'Cod_detencion_4', 'Cod_detencion_5', 'Cod_detencion_6',
                           'Desc_detencion', 'Comentarios']

    def read_line_data(self, line_name: str, year: str, month: str) -> pd.DataFrame:
        """Lee los datos de una línea específica para un mes y año."""
        path = os.path.join(self.base_path, line_name, year, month)
        
        if not os.path.exists(path):
            print(f"No se encontró el directorio: {path}")
            return None

        csv_files = [f for f in os.listdir(path) if f.endswith('.csv')]
        if not csv_files:
            print(f"No se encontraron archivos CSV en: {path}")
            return None

        dfs = []
        for file in csv_files:
            file_path = os.path.join(path, file)
            try:
                # Leer CSV con pandas
                df = pd.read_csv(file_path, 
                               sep=";",
                               encoding='utf-8',
                               na_values=["", "null", "NA", "N/A"],
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
                                   'code_u': str,
                                   'code_f': str,
                                   'code_t': str,
                                   'code_l': str,
                                   'code_m': str,
                                   'code_n': str
                               })
                dfs.append(df)
            except Exception as e:
                print(f"Error leyendo {file}: {e}")
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
        df['Cod_detencion_2'] = df['Cod_detencion_2'].replace(
            periodo_variantes, 
            'Período no laboral'
        )

        # Convertir las columnas de fecha a datetime
        df['Fecha_Hora_Inicio'] = pd.to_datetime(df['Fecha_Hora_Inicio'])
        df['Fecha_Hora_Termino'] = pd.to_datetime(df['Fecha_Hora_Termino'])
        
        # Crear máscaras para los criterios
        es_termoformado = df['Linea'] == 'Termoformado'
        es_no_asignado = df['Cod_detencion_3'] == 'No asignado'
        
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
        df.loc[mask_turno_no_usado, 'Cod_detencion_3'] = 'Turno No Usado'
        
        # Eliminar columnas auxiliares
        df = df.drop(['dia_semana', 'hora'], axis=1)

        # Crear columna tipo_registro_1 con clasificación unificada
        codigos_planificados = ['Planificado', 'Período no laboral', 'Externa']
        
        # Inicializar con valor por defecto
        df['tipo_registro_1'] = df['Cod_detencion_2'].apply(
            lambda x: 'Planificado' if pd.notna(x) and x in codigos_planificados
            else ('No planificado' if pd.notna(x) and x.strip() != ''
            else 'Producción')
        )
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

    def process_line(self, line_name: str, year: str) -> pd.DataFrame:
        """Procesa todos los meses de una línea para un año específico."""
        yearly_data = []
        
        for month in [f"{m:02d}" for m in range(1, 13)]:
            print(f"Procesando línea {line_name} - {year}/{month}")
            monthly_df = self.read_line_data(line_name, year, month)
            
            if monthly_df is not None:
                # Añadir columnas de identificación
                monthly_df['Mes'] = int(month)
                monthly_df['Año'] = int(year)
                
                # Aplicar transformaciones
                monthly_df = self.transform_data(monthly_df)
                yearly_data.append(monthly_df)

        return pd.concat(yearly_data, ignore_index=True) if yearly_data else None

def main():
    # Configuración
    base_path = path_folder
    lines = ["TF_2","TF_3","TF_5","TF_6","TF_7","TF_8","TF_9","TF_10","EXT_14","EXT_15","EXT_16"]
    year = "2025"
    
    # Crear instancia del transformador
    transformer = DataTransformer(base_path)
    
    # Procesar cada línea
    all_data = []
    for line in lines:
        line_data = transformer.process_line(line, year)
        if line_data is not None:
            all_data.append(line_data)
    
    if all_data:
        # Consolidar todos los datos
        final_df = pd.concat(all_data, ignore_index=True)
        
        # Crear directorio de salida
        output_dir = os.path.join(base_path, "consolidated")
        os.makedirs(output_dir, exist_ok=True)
        
        # Guardar resultado
        output_path = os.path.join(output_dir, f"mt_typ_consolidado_{year}_test2.csv")
        final_df.to_csv(output_path, sep=";", index=False, encoding='utf-8-sig')
        print(f"Datos consolidados guardados en: {output_path}")
        print(f"Total de registros procesados: {len(final_df)}")
    else:
        print("No se encontraron datos para procesar")

if __name__ == "__main__":
    main()