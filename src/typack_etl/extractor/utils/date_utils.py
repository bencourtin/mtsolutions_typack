from datetime import datetime

def validar_formato_fecha(fecha_str):
    """
    Valida que una cadena tenga el formato esperado: 'AAAA-MM-DD HH:MM:SS'.
    Retorna True si es válido, False en caso contrario.
    """
    try:
        datetime.strptime(fecha_str, "%Y-%m-%d %H:%M:%S")
        return True
    except ValueError:
        return False
