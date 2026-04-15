"""
Módulo de prueba para dividir registros que cruzan medianoche.
Divide registros en dos cuando cruzan el cambio de día y recalcula producción proporcionalmente.
"""

import os
import pandas as pd
from datetime import datetime, time, timedelta
from pathlib import Path

from typack_etl.transformer.transformer import DataTransformer


def split_midnight_crossings(df: pd.DataFrame) -> pd.DataFrame:
    """
    Divide registros que cruzan medianoche en dos registros separados.
    Redistribuye la producción proporcionalmente al tiempo trabajado en cada tramo.
    
    Args:
        df: DataFrame con los registros originales
        
    Returns:
        DataFrame con registros divididos donde cruzan medianoche
    """
    if df is None or df.empty:
        return df
    
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
                prod_B = prod_original - prod_A  # Garantizar suma exacta
            else:
                prod_A = 0
                prod_B = 0
            
            # Crear registro A (mismo día hasta 23:59:59)
            record_A = row.copy()
            record_A['Fecha_Hora_Termino'] = fin_dia_inicio
            record_A['Duracion(min)'] = round(dur_A, 2)
            record_A['Q_prod_total'] = prod_A
            new_records.append(record_A)
            
            # Crear registro B (día siguiente desde 00:00:00)
            record_B = row.copy()
            record_B['Fecha_Hora_Inicio'] = inicio_dia_siguiente
            record_B['Fecha_Hora_Termino'] = fin
            record_B['Duracion(min)'] = round(dur_B, 2)
            record_B['Q_prod_total'] = prod_B
            
            # Actualizar Mes y Año si cambió
            record_B['Mes'] = fin.month
            record_B['Año'] = fin.year
            
            new_records.append(record_B)
            
        else:
            # No cruza medianoche, mantener registro original
            new_records.append(row)
    
    # Crear nuevo DataFrame con los registros procesados
    result_df = pd.DataFrame(new_records)
    
    # Resetear índice
    result_df = result_df.reset_index(drop=True)
    
    return result_df


def recalculate_minutos_extras(df: pd.DataFrame, transformer: DataTransformer) -> pd.DataFrame:
    """
    Recalcula los minutos extras después de dividir registros.
    
    Args:
        df: DataFrame con registros divididos
        transformer: Instancia de DataTransformer con horarios cargados
        
    Returns:
        DataFrame con minutos extras recalculados
    """
    if df is None or df.empty:
        return df
    
    # Recalcular Minutos_Extras para cada registro
    if 'Minutos_Extras' in df.columns:
        df['Minutos_Extras'] = df.apply(transformer._calcular_minutos_extras, axis=1)
    
    # Recalcular Periodo_Laboral si existe
    if 'Periodo_Laboral' in df.columns:
        df['Periodo_Laboral'] = df.apply(transformer._determinar_periodo_laboral, axis=1)
    
    return df


def test_midnight_split():
    """
    Función de prueba para demostrar la división de registros en medianoche.
    """
    print("=" * 80)
    print("PRUEBA: División de registros que cruzan medianoche")
    print("=" * 80)
    
    # Crear instancia del transformador para acceder a funciones auxiliares
    transformer = DataTransformer()
    
    # Leer el archivo consolidado (o puedes especificar otro archivo de prueba)
    consolidated_path = transformer.consolidated_path
    consolidated_file = os.path.join(consolidated_path, "mt_typ_consolidated.csv")
    
    if not os.path.exists(consolidated_file):
        print(f"❌ No se encontró el archivo consolidado: {consolidated_file}")
        print("Por favor ejecuta primero el transformer principal.")
        return
    
    print(f"\n📂 Leyendo archivo: {consolidated_file}")
    
    # Leer datos
    df = pd.read_csv(consolidated_file, 
                     sep=";",
                     encoding='utf-8',
                     decimal=',')
    
    # Convertir columnas de fecha
    df['Fecha_Hora_Inicio'] = pd.to_datetime(df['Fecha_Hora_Inicio'])
    df['Fecha_Hora_Termino'] = pd.to_datetime(df['Fecha_Hora_Termino'])
    
    print(f"📊 Total registros originales: {len(df):,}")
    
    # Contar registros que cruzan medianoche
    midnight_crossings = df[df['Fecha_Hora_Inicio'].dt.date != df['Fecha_Hora_Termino'].dt.date]
    print(f"🌙 Registros que cruzan medianoche: {len(midnight_crossings):,}")
    
    if len(midnight_crossings) > 0:
        print("\n📋 Ejemplo de registros que cruzan medianoche (primeros 3):")
        print(midnight_crossings[['Fecha_Hora_Inicio', 'Fecha_Hora_Termino', 'Q_prod_total', 'Turno', 'Linea']].head(3).to_string())
    
    # Aplicar división de registros
    print("\n⚙️  Aplicando división de registros...")
    df_split = split_midnight_crossings(df)
    
    print(f"📊 Total registros después de división: {len(df_split):,}")
    print(f"📈 Diferencia: +{len(df_split) - len(df):,} registros")
    
    # Recalcular minutos extras
    print("\n⚙️  Recalculando minutos extras y periodo laboral...")
    df_split = recalculate_minutos_extras(df_split, transformer)
    
    # Validaciones
    print("\n✅ Validaciones:")
    
    # Verificar que no hay registros con inicio > fin
    invalid_times = df_split[df_split['Fecha_Hora_Inicio'] > df_split['Fecha_Hora_Termino']]
    print(f"   - Registros con inicio > fin: {len(invalid_times)}")
    
    # Verificar suma de producción (comparar antes y después para registros divididos)
    original_prod_total = df['Q_prod_total'].sum()
    split_prod_total = df_split['Q_prod_total'].sum()
    diff = abs(original_prod_total - split_prod_total)
    print(f"   - Producción original: {original_prod_total:,.2f}")
    print(f"   - Producción después de split: {split_prod_total:,.2f}")
    print(f"   - Diferencia: {diff:.2f} ({'✅ OK' if diff < 0.01 else '⚠️  REVISAR'})")
    
    # Guardar archivo de prueba
    output_file = os.path.join(consolidated_path, "mt_typ_consolidated_midnight_split_TEST.csv")
    print(f"\n💾 Guardando archivo de prueba: {output_file}")
    df_split.to_csv(output_file, sep=";", index=False, encoding='utf-8-sig', decimal=',')
    
    print("\n" + "=" * 80)
    print("✅ PRUEBA COMPLETADA")
    print("=" * 80)
    print(f"\n📁 Archivo generado: {output_file}")
    print("\nPuedes comparar ambos archivos para verificar los cambios:")
    print(f"  - Original: {consolidated_file}")
    print(f"  - Con split: {output_file}")
    
    # Mostrar ejemplos de registros divididos
    if len(midnight_crossings) > 0:
        print("\n📋 Ejemplo de división (primer registro que cruzaba medianoche):")
        first_crossing = midnight_crossings.iloc[0]
        first_inicio = first_crossing['Fecha_Hora_Inicio']
        first_fin = first_crossing['Fecha_Hora_Termino']
        
        # Buscar los registros correspondientes en el resultado
        split_records = df_split[
            (df_split['Fecha_Hora_Inicio'] >= first_inicio - timedelta(seconds=1)) &
            (df_split['Fecha_Hora_Inicio'] <= first_inicio + timedelta(seconds=1))
        ].head(2)
        
        print("\nOriginal:")
        print(first_crossing[['Fecha_Hora_Inicio', 'Fecha_Hora_Termino', 'Q_prod_total', 'Duracion(min)', 'Turno']].to_string())
        
        print("\nDividido en:")
        if len(split_records) > 0:
            print(split_records[['Fecha_Hora_Inicio', 'Fecha_Hora_Termino', 'Q_prod_total', 'Duracion(min)', 'Turno']].to_string())


if __name__ == "__main__":
    test_midnight_split()
