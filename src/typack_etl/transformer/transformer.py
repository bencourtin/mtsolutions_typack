import os
import argparse
import pandas as pd
from datetime import datetime
from typing import List, Dict
from pathlib import Path
from tqdm import tqdm

from config.settings import PATHS, load_horarios, load_lineas
from typack_etl.extractor.utils.date_utils import CORTE_LOCAL, a_local
from typack_etl.transformer.turnos import Caso, Horarios, sin_horas_extra


def _a_hora_local(serie: pd.Series) -> pd.Series:
    """Reloj del proveedor -> hora local de planta.

    El raw guarda los timestamps tal como los manda la API. Ese reloj es hora
    chilena, pero con las reglas de horario de verano que el país usó hasta
    2018, así que va una hora adelantado durante unas nueve semanas al año
    (ver `date_utils.a_local`). En esas ventanas, sin esta conversión, toda
    regla horaria de más abajo —turno, minutos extras, día operativo, fin de
    semana— queda corrida.

    Se convierte acá, en la frontera de entrada, y no en el raw: la identidad
    de un segmento en el libro de auditoría es sha1(devid + start + end) con
    los valores del proveedor, así que reescribir el raw re-keyearía todo el
    histórico.
    """
    return serie.map(lambda momento: a_local(momento) if pd.notna(momento) else pd.NaT)


def _get_month_name_spanish(month_num: int) -> str:
    """Convierte número de mes (1-12) a nombre en español."""
    month_names = {
        1: "enero", 2: "febrero", 3: "marzo", 4: "abril",
        5: "mayo", 6: "junio", 7: "julio", 8: "agosto",
        9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre"
    }
    return month_names.get(month_num, str(month_num))


class DataTransformer:
    """Aplica las reglas de negocio sobre los datos crudos de la sábana.

    Las tres rutas son parametrizables. `base_path` por sí solo NO aísla el
    transformador: las salidas siguen saliendo de PATHS, o sea de producción.
    Para trabajar sobre datos de prueba hay que pasar también
    `processed_path` y `consolidated_path`.
    """

    def __init__(
        self,
        base_path: str = None,
        test_mode: bool = False,
        processed_path: str = None,
        consolidated_path: str = None,
    ):
        # Load config and use raw path if base_path not provided
        self.horarios = Horarios(load_horarios())
        self.test_mode = test_mode
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
            raw_path = PATHS.get("raw", "data/raw")
            self.base_path = os.path.normpath(raw_path.replace('\\', os.sep))
        
        # Rutas de salida: explícitas > modo test > configuración
        def _salida(explicita, clave, default):
            if explicita:
                return os.path.normpath(str(explicita).replace('\\', os.sep))
            ruta = PATHS.get(clave, default).replace('\\', os.sep).rstrip(os.sep)
            return os.path.normpath(ruta + "_test" if test_mode else ruta)

        self.processed_path = _salida(processed_path, "processed", "data/processed")
        self.consolidated_path = _salida(consolidated_path, "consolidated", "data/consolidated")
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
            'code_n': 'Cod_detencion_6',
            # Trazabilidad hacia el libro de auditoría (ver typack_etl.audit.traza)
            'record_key': 'Record_Key',
            'run_id_alta': 'Run_Id_Alta',
            'run_id_version': 'Run_Id_Version',
            'reasignada': 'Reasignada',
            'fecha_reasignacion': 'Fecha_Reasignacion',
        }
        self.columns_to_drop = ['end_utc', 'start_utc']
        # Columnas que deben quedar como texto para poder usar el accesor .str
        self.text_columns = ['Producto', 'Desc_detencion', 'Sku', 'Comentarios', 'Planta',
                             'Tipo_Unidad', 'Usuario', 'Maquina', 'Linea', 'Turno',
                             'Cod_detencion_1', 'Cod_detencion_2', 'Cod_detencion_3',
                             'Cod_detencion_4', 'Cod_detencion_5', 'Cod_detencion_6']
        self.column_order = ['Año', 'Mes', 'Dia_Operativo', 'Fecha_Hora_Inicio', 'Fecha_Hora_Termino', 'Planta',
                           'Linea', 'Maquina', 'Turno', 'OF', 'Usuario', 'Producto', 'Sku', 'Tipo_Unidad',
                           'Q_prod_total', 'Duracion(min)', 'Cod_detencion_1', 'Cod_detencion_2',
                           'Cod_detencion_3', 'Cod_detencion_4', 'Cod_detencion_5', 'Cod_detencion_6',
                           'Desc_detencion', 'Comentarios',
                           'Reasignada', 'Fecha_Reasignacion',
                           'Record_Key', 'Run_Id_Alta', 'Run_Id_Version']

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

        # Normalizar a texto las columnas de código y descripción. Una columna
        # enteramente vacía la lee pandas como float64, y entonces el accesor
        # .str que usan las reglas de más abajo falla. Antes esto ocurría de
        # rebote al reconstruir el DataFrame en el split de medianoche; ahora
        # que el split no existe, se hace explícito.
        for col in self.text_columns:
            if col in df.columns:
                df[col] = df[col].astype(object).where(df[col].notna(), pd.NA)

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

        # De acá para abajo, todo está en hora local de planta: es el reloj de
        # los turnos y el único en que las reglas horarias son correctas.
        df['Fecha_Hora_Inicio'] = _a_hora_local(df['Fecha_Hora_Inicio'])
        df['Fecha_Hora_Termino'] = _a_hora_local(df['Fecha_Hora_Termino'])

        # El turno sale de la hora de inicio, ya en hora local. Sólo se calcula
        # donde el origen no sabe (T0, '-', vacío); un turno que ya vino del
        # proveedor se respeta, salvo en el hueco 18:00-23:00 de Termoformado.
        # Ver `transformer.turnos` para las tres reglas y por qué no viven en
        # el JSON.
        clasificado = [
            self.horarios.clasificar(linea, turno, inicio)
            for linea, turno, inicio in zip(df['Linea'], df['Turno'], df['Fecha_Hora_Inicio'])
        ]
        df['Turno'] = [c[0] for c in clasificado]
        # `_caso_turno` es de trabajo: dice de dónde salió el turno, lo usa el
        # cálculo de horas extra y se descarta antes de guardar.
        df['_caso_turno'] = [c[1] for c in clasificado]
        self._avisar_turnos_sin_resolver(df)

        # Año y Mes operativos, no de calendario: el día operativo va de corte a
        # corte (08:00 locales, el cambio de turno TD/TN), de modo que el turno
        # de noche del último día del mes cuenta entero en ese mes. Coincide con
        # el particionado de archivos que hace el extractor, que hace el mismo
        # corte en el reloj de la API.
        dia_op = df['Fecha_Hora_Inicio'] - pd.Timedelta(hours=CORTE_LOCAL.hour, minutes=CORTE_LOCAL.minute)
        df['Año'] = dia_op.dt.year
        df['Mes'] = dia_op.dt.month
        df['Dia_Operativo'] = dia_op.dt.date

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

        # `_caso_turno` era de trabajo: ya lo consumió el cálculo de extras y
        # no forma parte del esquema que lee el BI.
        df = df.drop(columns=['_caso_turno'], errors='ignore')

        # Reordenar columnas (solo las que existen en el DataFrame)
        valid_columns = [col for col in self.column_order if col in df.columns]
        remaining_columns = [col for col in df.columns if col not in valid_columns and col != 'Tipo_registro']
        df = df[valid_columns + ['Tipo_registro'] + remaining_columns]

        return df
    
    def _avisar_turnos_sin_resolver(self, df) -> None:
        """Informa los turnos que no se pudieron calcular con el config.

        Son casos que el JSON no cubre, no errores de datos: si quedan muchos,
        falta cargar una vigencia o los días de fin de semana.
        """
        casos = df['_caso_turno'].value_counts()
        sin_vigencia = int(casos.get(Caso.SIN_VIGENCIA, 0))
        fin_de_semana = int(casos.get(Caso.FIN_DE_SEMANA, 0))
        if sin_vigencia:
            primera = df.loc[df['_caso_turno'] == Caso.SIN_VIGENCIA, 'Fecha_Hora_Inicio'].min()
            print(f"  AVISO: {sin_vigencia} filas sin vigencia aplicable en horarios.json "
                  f"(la más antigua, {primera}). Conservan el turno de origen.")
        if fin_de_semana:
            print(f"  AVISO: {fin_de_semana} filas de sábado/domingo sin turno de origen; "
                  "horarios.json no define esos días. Se clasifican como TD sin horas extra.")

    def _calcular_minutos_extras(self, row) -> float:
        """Calcula los minutos extras trabajados después del horario de término.
        Solo aplica para línea TERMOFORMADO con Q_prod_total > 0.
        Si el TD (Turno Día) cae en fin de semana, todo el tiempo trabajado es considerado extra.
        """
        minutos_extras = 0
        linea = row['Linea']
        
        # Solo aplicar la lógica a la línea TERMOFORMADO
        if linea.upper().strip() != 'TERMOFORMADO':
            return minutos_extras

        # Fin de semana y filas sin vigencia: el turno lo pusimos nosotros sin
        # un horario del config detrás, así que no hay fin de jornada contra el
        # cual medir el exceso. Decisión del 2026-09-16.
        if sin_horas_extra(row.get('_caso_turno', '')):
            return minutos_extras
        
        # Solo aplicar si hay producción (Q_prod_total > 0)
        if pd.isna(row['Q_prod_total']) or row['Q_prod_total'] <= 0:
            return minutos_extras
        
        inicio = row['Fecha_Hora_Inicio']
        termino = row['Fecha_Hora_Termino']
        turno_actual = row['Turno']
        
        # Si TD cae en fin de semana (sábado=5, domingo=6), todo el tiempo es extra
        if turno_actual == 'TD':
            day_of_week = inicio.weekday()
            if day_of_week >= 5:  # Sábado o domingo
                minutos_extras = (termino - inicio).total_seconds() / 60
                return minutos_extras

        # El fin sale del JSON, no de la extensión del hueco: el Día de
        # Termoformado se clasifica hasta las 23:00 pero termina oficialmente a
        # las 18:00, y lo trabajado después de esa hora es justamente lo extra.
        hora_fin = self.horarios.fin_de_turno(linea, turno_actual, inicio)
        if hora_fin is None:
            return minutos_extras

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
        
        franja = self.horarios.franja(turno_actual, row['Fecha_Hora_Inicio'])
        if franja is None:
            return None
        return franja[1].strftime("%H:%M")
    
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

        franja = self.horarios.franja(turno_actual, inicio)
        if franja is None:
            return None
        arranca, termina = franja

        hora_inicio_turno = datetime.combine(inicio.date(), arranca)
        hora_fin_turno = datetime.combine(inicio.date(), termina)

        # Ajustar para turnos nocturnos
        if hora_fin_turno <= hora_inicio_turno:
            hora_fin_turno += pd.Timedelta(days=1)
            # Si estamos en el día siguiente y el inicio es después de medianoche
            if inicio.time() < arranca:
                hora_inicio_turno -= pd.Timedelta(days=1)

        # Verificar si está dentro del horario laboral
        if hora_inicio_turno <= inicio <= hora_fin_turno:
            return 'Sí'
        else:
            return 'No'
    
    
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
    
    def _needs_processing(self, line_name: str, year: str, month: str) -> bool:
        """Verifica si un archivo raw es más reciente que su procesado (para modo incremental)."""
        # Buscar archivos raw
        raw_dir = os.path.normpath(os.path.join(self.base_path, year, f"{int(month):02d}"))
        if not os.path.exists(raw_dir):
            return False

        month_name = _get_month_name_spanish(int(month))
        line_pattern = f"produccion_{year}_{month_name}_{line_name.lower()}"
        raw_files = [os.path.join(raw_dir, f) for f in os.listdir(raw_dir)
                     if f.endswith('.csv') and line_pattern in f.lower()]
        if not raw_files:
            return False

        # Buscar archivo procesado correspondiente
        processed_dir = os.path.join(self.processed_path, year, f"{int(month):02d}")
        processed_file = os.path.join(processed_dir, f"processed_prod_{year}_{month_name}_{line_name.lower()}.csv")

        if not os.path.exists(processed_file):
            return True  # No existe procesado, hay que procesar

        # Comparar timestamps: si algún raw es más reciente que el procesado, reprocesar
        processed_mtime = os.path.getmtime(processed_file)
        for raw_file in raw_files:
            if os.path.getmtime(raw_file) > processed_mtime:
                return True

        return False

    def process_line(self, line_name: str, year: str, pbar: tqdm = None, incremental: bool = False) -> pd.DataFrame:
        """Procesa todos los meses de una línea para un año específico y guarda archivos procesados.
        
        Args:
            line_name: Nombre de la línea a procesar.
            year: Año a procesar.
            pbar: Barra de progreso opcional.
            incremental: Si True, solo procesa meses con datos raw más recientes que los procesados.
        """
        processed_files = []
        
        for month in [f"{m:02d}" for m in range(1, 13)]:
            if pbar:
                pbar.set_description(f"Procesando {line_name}-{month}")
            
            # En modo incremental, saltar si no hay cambios
            if incremental and not self._needs_processing(line_name, year, month):
                if pbar:
                    pbar.update(1)
                continue

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
    parser = argparse.ArgumentParser(description="Transformer ETL Typack")
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="Solo procesa archivos raw más recientes que sus procesados. Reduce tiempo de ejecución."
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Modo testing: guarda resultados en directorios separados (*_test) para validar reglas de negocio sin afectar producción."
    )
    parser.add_argument("--input", required=False, help="Directorio raw de entrada (default: PATH_RAW)")
    parser.add_argument("--processed", required=False, help="Directorio de salida procesada (default: PATH_PROCESSED)")
    parser.add_argument("--consolidated", required=False, help="Directorio del consolidado (default: PATH_CONSOLIDATED)")
    args = parser.parse_args()

    # Las tres rutas son parametrizables: apuntar sólo la entrada dejaba las
    # salidas en producción, que es como se sobrescriben datos reales sin querer.
    if args.input and not (args.processed and args.consolidated):
        parser.error("--input requiere también --processed y --consolidated, "
                     "para no escribir las salidas en producción.")

    # Una sola fuente para la lista de líneas: config/lineas.json. Antes estaba
    # duplicada acá, así que dar de alta una línea requería dos ediciones.
    lines = [linea["name"] for linea in load_lineas()]

    transformer = DataTransformer(
        base_path=args.input,
        test_mode=args.test,
        processed_path=args.processed,
        consolidated_path=args.consolidated,
    )
    print(f"Entrada    : {transformer.base_path}")
    print(f"Procesados : {transformer.processed_path}")
    print(f"Consolidado: {transformer.consolidated_path}\n")

    mode_label = []
    if args.incremental:
        mode_label.append("INCREMENTAL")
    if args.test:
        mode_label.append("TEST")
    if mode_label:
        print(f"Modo: {' + '.join(mode_label)}")
        if args.test:
            print(f"  Salida procesados: {transformer.processed_path}")
            print(f"  Salida consolidado: {transformer.consolidated_path}")
    
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
                transformer.process_line(line, year, pbar, incremental=args.incremental)
    
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