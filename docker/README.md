# Docker Configuration for TYPACK ETL

Este directorio contiene la configuración Docker para el proyecto TYPACK ETL.

## Estructura de archivos

```
docker/
├── extractor.dockerfile    # Dockerfile para servicios de extracción
├── transformer.dockerfile  # Dockerfile para servicios de transformación
├── docker-compose.yaml     # Configuración de producción
├── docker-compose.dev.yaml # Configuración de desarrollo
└── README.md              # Este archivo
```

## Servicios disponibles

### Extractor Services
- **extractor_daily**: Extrae datos diarios desde la API
- **extractor_30m**: Extrae datos de los últimos 30 días
- **extractor_mensual**: Extrae datos mensuales

### Transformer Services
- **transformer**: Procesa y transforma los datos extraídos

## Comandos básicos

### Construcción de imágenes
```bash
# Construir todos los servicios
docker-compose build

# Construir solo el extractor
docker-compose build extractor_daily

# Construir solo el transformer
docker-compose build transformer
```

### Ejecución de servicios

```bash
# Ejecutar todos los servicios
docker-compose up

# Ejecutar en background
docker-compose up -d

# Ejecutar servicio específico
docker-compose up extractor_daily

# Ver logs de un servicio
docker-compose logs -f extractor_daily
```

### Desarrollo

```bash
# Usar configuración de desarrollo
docker-compose -f docker-compose.dev.yaml up

# Ejecutar comando específico en contenedor
docker-compose exec extractor_daily bash
```

### Limpieza

```bash
# Detener todos los servicios
docker-compose down

# Detener y eliminar volúmenes
docker-compose down -v

# Limpiar imágenes no utilizadas
docker system prune
```

## Volúmenes

Los siguientes directorios se montan como volúmenes:
- `./data`: Datos procesados y extraídos
- `./logs`: Archivos de log
- `./config`: Archivos de configuración

## Variables de entorno

- `PYTHONUNBUFFERED=1`: Output inmediato de Python
- `TZ=America/Santiago`: Timezone de Chile
- `PYTHONPATH=/app/src`: Path de módulos Python

## Configuración de red

Los servicios se ejecutan en una red interna `typack_network` para comunicación entre contenedores.

## Logs y monitoreo

Los logs se almacenan en:
- Logs de aplicación: `./logs/`
- Logs de Docker: `docker-compose logs [service_name]`

## Troubleshooting

### Problemas comunes

1. **Error de permisos**:
   ```bash
   sudo chown -R $(whoami):$(whoami) ./data ./logs
   ```

2. **Puerto ocupado**:
   - Modificar ports en docker-compose.yaml

3. **Memoria insuficiente**:
   - Agregar límites de memoria en docker-compose.yaml

4. **Dependencias faltantes**:
   - Verificar requirements.txt
   - Reconstruir imagen: `docker-compose build --no-cache`

### Debug

```bash
# Ejecutar bash en contenedor
docker-compose exec extractor_daily bash

# Ver procesos en contenedor
docker-compose exec extractor_daily ps aux

# Ver variables de entorno
docker-compose exec extractor_daily env
```