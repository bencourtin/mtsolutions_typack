# 📊 TYPACK Extractors Documentation

Este directorio contiene los extractores optimizados y refactorizados para el sistema TYPACK ETL.

## 🏗️ Arquitectura Modular

Los extractores han sido refactorizados con funciones modulares reutilizables ubicadas en la carpeta `functions/`:

### 📁 Functions (Módulos Reutilizables)

- **`config_manager.py`** - Gestión de configuraciones JSON
- **`date_scanner.py`** - Escaneo de fechas en archivos existentes  
- **`plan_manager.py`** - Generación de planes de descarga
- **`downloader.py`** - Descarga de datos desde la API
- **`log_utils.py`** - Utilidades de manejo de logs

## 🔧 Extractores Disponibles

### 1. **Extractor Diario (`extractor_daily.py`)**
Extractor incremental que descarga solo las fechas faltantes.

```bash
# Extracción normal (hasta ayer)
python extractor_daily.py

# Solo mostrar estado actual
python extractor_daily.py --status

# Modo dry-run (solo plan)
python extractor_daily.py --dry-run

# Para líneas sin datos, empezar desde fecha específica
python extractor_daily.py --first-date-if-empty 2024-01-01
```

### 2. **Extractor 30 Días (`extractor_30m.py`)**
Extractor de ventana móvil que asegura tener los últimos 30 días.

```bash
# Extracción de 30 días móviles
python extractor_30m.py

# Personalizar ventana (ej: 7 días)
python extractor_30m.py --days 7

# Ver cobertura actual
python extractor_30m.py --coverage

# Modo dry-run
python extractor_30m.py --dry-run
```

### 3. **Extractor Mensual (`extractor_mensual.py`)**
Extractor para meses completos específicos.

```bash
# Extraer mes actual hasta ayer
python extractor_mensual.py --current

# Extraer mes específico completo
python extractor_mensual.py --year 2024 --month 12

# Ver reporte de mes
python extractor_mensual.py --year 2024 --month 11 --report

# Extraer hasta día específico del mes
python extractor_mensual.py --year 2024 --month 12 --end-day 15
```

### 4. **Extractor Original (`extractor_daily_original.py`)**
Versión original sin refactorizar (mantenida para compatibilidad).

## 🚀 Características Mejoradas

### ✅ **Robustez**
- Sistema de reintentos automáticos
- Manejo inteligente de errores
- Validación de archivos existentes
- Renovación automática de tokens

### 📊 **Logging Mejorado**
- Logs estructurados por tipo de operación
- Limpieza automática de logs antiguos
- Reportes de progreso en tiempo real
- Resúmenes de cobertura

### ⚡ **Optimización**
- Detección de archivos existentes para evitar duplicados
- Descarga solo de datos faltantes
- Configuración flexible de parámetros
- Pausa inteligente entre descargas

### 🔧 **Flexibilidad**
- Múltiples modos de operación
- Configuración de fechas personalizada
- Soporte para dry-run
- Reportes de estado detallados

## 📋 Parámetros Comunes

Todos los extractores comparten algunos parámetros básicos:

- `--data-dir`: Directorio de datos personalizado
- `--dry-run`: Solo generar plan sin descargar
- `--tz`: Zona horaria (default: America/Santiago)

## 📁 Estructura de Salida

Los datos se organizan de la siguiente manera:

```
output_dir/
├── {linea}/
│   ├── {año}/
│   │   ├── {mes}/
│   │   │   ├── produccion_2024-01-15_{linea}.csv
│   │   │   └── ...
│   │   └── ...
│   └── ...
└── logs/
    ├── plan_incremental_{timestamp}.csv
    ├── plan_rolling_30d_{timestamp}.csv
    ├── results_{timestamp}.csv
    └── ...
```

## 🔄 Estrategias de Extracción

### **Incremental (Diario)**
- ✅ Eficiente para uso diario
- ✅ Solo descarga lo que falta
- ❌ Requiere ejecución regular

### **Ventana Móvil (30 días)**
- ✅ Asegura datos recientes completos
- ✅ Recupera automáticamente datos faltantes
- ⚠️ Puede re-descargar archivos existentes

### **Mensual Completo**
- ✅ Ideal para backfill de meses
- ✅ Control preciso de períodos
- ⚠️ Mayor volumen de datos

## 🛠️ Configuración

Los extractores leen configuración desde:

- `config/config.json` - Configuración principal
- `config/credenciales.json` - Credenciales API
- `config/lineas.json` - Configuración de líneas

## 📈 Monitoreo

### Logs de Plan
Registran qué se va a descargar:
- Línea, fecha y ruta de destino
- Útiles para auditoría y planificación

### Logs de Resultados
Registran el resultado de cada descarga:
- Éxitos y errores detallados
- Útiles para troubleshooting

### Reportes de Estado
- Cobertura por línea
- Días atrasados
- Estado general del sistema

## 🔧 Troubleshooting

### Errores Comunes

1. **Error de configuración**
   ```bash
   # Verificar que existan los archivos de config
   ls config/
   ```

2. **Error de permisos**
   ```bash
   # Verificar permisos de escritura
   python -c "import os; os.makedirs('test_write', exist_ok=True)"
   ```

3. **Error de API**
   - Verificar credenciales en `config/credenciales.json`
   - Verificar conectividad de red

4. **Error de importación**
   - Verificar que todas las dependencias estén instaladas
   - Verificar estructura de directorios

### Verificación de Salud

```bash
# Ver estado de todas las líneas
python extractor_daily.py --status

# Ver cobertura de 30 días
python extractor_30m.py --coverage

# Test de conexión (dry-run)
python extractor_daily.py --dry-run
```

## 📚 Ejemplos de Uso

### Escenario 1: Primera Ejecución
```bash
# Empezar desde fecha específica para todas las líneas
python extractor_daily.py --first-date-if-empty 2024-01-01
```

### Escenario 2: Recuperación de Datos
```bash
# Asegurar últimos 30 días completos
python extractor_30m.py
```

### Escenario 3: Backfill de Mes
```bash
# Completar diciembre 2024
python extractor_mensual.py --year 2024 --month 12
```

### Escenario 4: Monitoreo Regular
```bash
# Ejecución diaria automatizada
python extractor_daily.py
```