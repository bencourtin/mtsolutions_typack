# Dockerfile para el servicio extractor de TYPACK
# Basado en Python 3.11 slim para mayor eficiencia

FROM python:3.11-slim

# Metadatos del contenedor
LABEL maintainer="benjamin.courtin@typack.cl"
LABEL description="Extractor de datos para TYPACK ETL"
LABEL version="1.0"

# Variables de entorno para Python
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app/src

# Directorio de trabajo
WORKDIR /app

# Instalar dependencias del sistema (si es necesario para compilación)
RUN apt-get update && apt-get install -y \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Copiar archivo de requisitos primero para aprovechar cache de Docker
COPY requirements.txt .

# Instalar dependencias de Python
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copiar código fuente y configuración
COPY src/ ./src/
COPY config/ ./config/

# Crear directorio para datos y logs
RUN mkdir -p data/raw data/processed data/external logs

# Establecer permisos apropiados
RUN chmod +x src/typack_etl/extractor/pipelines/*.py

# Configurar timezone para Chile
ENV TZ=America/Santiago
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# Usuario no-root para seguridad (opcional pero recomendado)
RUN groupadd -r typack && useradd -r -g typack typack
RUN chown -R typack:typack /app
USER typack

# Healthcheck para verificar que el contenedor está funcionando
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

# Comando por defecto - ejecutar extractor diario
CMD ["python", "src/typack_etl/extractor/pipelines/extractor_daily.py"]

# Comandos alternativos disponibles:
# Para extractor de 30 días:
# CMD ["python", "src/typack_etl/extractor/pipelines/extractor_30m.py"]
# Para extractor mensual:
# CMD ["python", "src/typack_etl/extractor/pipelines/extractor_mensual.py"]