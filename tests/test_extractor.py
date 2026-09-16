"""
Script simple para extraer datos de la API de Typack a CSV.
Descarga varios períodos de prueba definidos en PERIODOS (hardcodeados).
Uso: python test_extractor.py
"""

import csv
import requests
from datetime import datetime
from pathlib import Path

from typack_etl.extractor.services.auth import get_token
from typack_etl.extractor.services.reports import get_sabana_report

# Períodos de prueba a descargar (fecha_inicio, fecha_fin, devid opcional)
# Formato de fecha/hora: YYYY-MM-DD HH:MM:SS
PERIODOS = [
    {"fecha_inicio": "2026-07-01 12:00:00", "fecha_fin": "2026-08-01 12:00:00", "devid": 1273},
    {"fecha_inicio": "2026-06-01 12:00:00", "fecha_fin": "2026-07-01 12:00:00", "devid": 1273},
    {"fecha_inicio": "2026-05-01 12:00:00", "fecha_fin": "2026-06-01 12:00:00", "devid": 1273},
    {"fecha_inicio": "2026-04-01 12:00:00", "fecha_fin": "2026-05-01 12:00:00", "devid": 1273},

]


def extraer_periodo(token, fecha_inicio, fecha_fin, devid=None):
    print()
    print("-" * 60)
    print(f"📅 Período: {fecha_inicio} a {fecha_fin}")
    print(f"🚌 Línea: {devid if devid else 'Todas'}")
    print("-" * 60)
    print()

    try:
        # Consultar API
        print("📡 Consultando API...")
        datos = get_sabana_report(token, fecha_inicio, fecha_fin, devid)
        print(f"✅ {len(datos)} registros obtenidos")

        if not datos:
            print("⚠️  No se encontraron datos para el período especificado")
            return

        # Generar nombre de archivo (fechas sin ":" ni espacios para que sea válido en el filesystem)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        devid_suffix = f"_{devid}" if devid else "_todas"
        inicio_safe = fecha_inicio.replace(" ", "_").replace(":", "")
        fin_safe = fecha_fin.replace(" ", "_").replace(":", "")
        nombre_archivo = f"test_extraccion{devid_suffix}_{inicio_safe}_a_{fin_safe}_{timestamp}.csv"
        ruta_archivo = Path("data/raw") / nombre_archivo

        # Crear directorio si no existe
        ruta_archivo.parent.mkdir(parents=True, exist_ok=True)

        # Guardar a CSV
        print(f"💾 Guardando en {nombre_archivo}...")

        # Obtener todas las claves únicas de los registros
        claves = set()
        for registro in datos:
            claves.update(registro.keys())
        claves = sorted(claves)

        with open(ruta_archivo, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=claves)
            writer.writeheader()
            writer.writerows(datos)

        print(f"✅ Archivo guardado: {ruta_archivo}")
        print()
        print(f"📊 Resumen:")
        print(f"   - Total registros: {len(datos)}")
        print(f"   - Columnas: {len(claves)}")
        print(f"   - Archivo: {ruta_archivo}")

    except requests.exceptions.RequestException as e:
        print(f"❌ Error de conexión: {e}")
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()


def main():
    print("=" * 60)
    print("EXTRACTOR DE DATOS TYPACK - MODO TEST")
    print("=" * 60)

    try:
        # Autenticación
        print()
        print("🔐 Autenticando...")
        token = get_token()
        print("✅ Token obtenido")

        for periodo in PERIODOS:
            extraer_periodo(
                token,
                periodo["fecha_inicio"],
                periodo["fecha_fin"],
                periodo.get("devid"),
            )

        print()
        print("✨ Extracción completada exitosamente!")

    except requests.exceptions.RequestException as e:
        print(f"❌ Error de conexión: {e}")
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
