# Transformer con config.json

## Descripción

El `transformer.py` ha sido actualizado para replicar la funcionalidad del `extractor_mensual.py`:

### ✅ Cambios Implementados

1. **Configuración desde config.json**
   - Usa automáticamente las rutas definidas en `config.json`
   - Carga rutas para `raw`, `processed`, `consolidated`, `logs`
   - Funciona tanto local como en Docker

2. **Estructura de Directorios año/mes**
   - Archivos procesados se guardan en: `processed/{año}/{mes}/`
   - Coincide con la estructura del `extractor_mensual.py`

3. **Nuevo Formato de Nombres**
   - **Antes**: `mt_typ_consolidado_{año}_.csv`
   - **Ahora**: `processed_prod_{año}_{mes}_{linea}.csv`
   - Ejemplo: `processed_prod_2025_octubre_tf_2.csv`

4. **Procesamiento Individual por Línea/Mes**
   - Cada línea y mes genera un archivo procesado independiente
   - Permite procesamiento granular y troubleshooting

5. **Consolidación Automática**
   - Lee todos los archivos `processed_prod_*` de la estructura año/mes
   - Consolida en un único archivo final
   - Guarda en la ruta `consolidated` del config.json

## 🔧 Funcionamiento

### Flujo de Proceso
```
1. Lee datos raw de: {raw}/{año}/{mes}/produccion_{año}_{mes}_{linea}.csv
2. Procesa cada archivo individualmente
3. Guarda en: {processed}/{año}/{mes}/processed_prod_{año}_{mes}_{linea}.csv
4. Al final, consolida todos en: {consolidated}/mt_typ_consolidado_{año}_.csv
```

### Ejemplo de Salida
```
=== Procesando línea TF_2 ===
Procesando línea TF_2 - 2025/01
Datos procesados guardados: G:\...\processed\2025\01\processed_prod_2025_enero_tf_2.csv
Procesando línea TF_2 - 2025/02
Datos procesados guardados: G:\...\processed\2025\02\processed_prod_2025_febrero_tf_2.csv
...

=== Iniciando consolidación ===
Leído: G:\...\processed\2025\01\processed_prod_2025_enero_tf_2.csv (1234 registros)
Leído: G:\...\processed\2025\02\processed_prod_2025_febrero_tf_2.csv (1456 registros)
...
Datos consolidados guardados en: G:\...\consolidated\mt_typ_consolidado_2025_.csv
Total de registros consolidados: 45678
```

## 🐳 Docker

El `transformer.dockerfile` también se actualizó para soportar las rutas de `config.json`:

```yaml
# En docker-compose.yaml - los volúmenes ya están configurados para transformer
volumes:
  - "G:/Unidades compartidas/.../raw:/mnt/typack/raw"
  - "G:/Unidades compartidas/.../processed:/mnt/typack/processed"
  - "G:/Unidades compartidas/.../consolidated:/mnt/typack/consolidated"
  - "G:/Unidades compartidas/.../logs:/mnt/typack/logs"
```

## 🚀 Comandos

### Local
```powershell
# Ejecutar transformer
python src/typack_etl/transformer/transformer.py
```

### Docker
```powershell
# Construir y ejecutar
docker-compose build transformer
docker-compose run --rm transformer
```

## 📁 Estructura Final

```
G:/Unidades compartidas/.../Data_MTSolutions/
├── raw/
│   └── 2025/
│       ├── 01/produccion_2025_enero_tf_2.csv
│       ├── 02/produccion_2025_febrero_tf_2.csv
│       └── ...
├── processed/
│   └── 2025/
│       ├── 01/processed_prod_2025_enero_tf_2.csv
│       ├── 02/processed_prod_2025_febrero_tf_2.csv
│       └── ...
└── consolidated/
    └── mt_typ_consolidado_2025_.csv
```