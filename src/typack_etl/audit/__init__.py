"""
Auditoría de cambios sobre la sábana de detenciones.

La API de MT Solutions no entrega un hecho inmutable sino el estado actual de
la sábana en el instante de la consulta: una detención puede reclasificarse
días después del evento. Este paquete detecta esos cambios comparando cada
extracción contra lo que el archivo de la partición ya tenía, y los registra en
un libro append-only.
"""
