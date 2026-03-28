# Referencia Satelital - Proyecto Boomerang

## Cobertura Temporal por Sensor

| Sensor | Resolucion | Periodo | Coleccion GEE | Notas |
|--------|-----------|---------|---------------|-------|
| Landsat 5 TM | 30m | 2008-2012 | `LANDSAT/LT05/C02/T1_L2` | Fin de vida 2012 |
| Landsat 7 ETM+ | 30m | 2008-2024 | `LANDSAT/LE07/C02/T1_L2` | SLC-off desde 2003 (striping) |
| Landsat 8 OLI | 30m | 2013-presente | `LANDSAT/LC08/C02/T1_L2` | Datos limpios |
| Landsat 9 OLI-2 | 30m | 2021-presente | `LANDSAT/LC09/C02/T1_L2` | Complementa Landsat 8 |
| Sentinel-2 MSI | 10m | 2015-presente | `COPERNICUS/S2_SR_HARMONIZED` | Mejor resolucion, bandas Red Edge |

Collection 2 de USGS ya esta armonizada entre sensores Landsat. No requiere
correccion radiometrica adicional para combinar series temporales.

## Estrategia por Periodo

- **2008-2012**: Landsat 5 TM (primario) + Landsat 7 ETM+ (complemento, cuidado con striping)
- **2013-2015**: Landsat 8 OLI (datos limpios, transicion)
- **2015-2024**: Landsat 8/9 (30m temporal) + Sentinel-2 (10m detalle)
- **Clasificacion actual**: Sentinel-2 SR Harmonized (10m)

## Mapeo de Bandas entre Sensores

### Bandas Opticas Principales

| Banda | Landsat 5 TM | Landsat 7 ETM+ | Landsat 8/9 OLI | Sentinel-2 MSI |
|-------|-------------|----------------|-----------------|----------------|
| Blue | B1 (0.45-0.52) | B1 (0.45-0.52) | B2 (0.45-0.51) | B2 (0.46-0.52) |
| Green | B2 (0.52-0.60) | B2 (0.52-0.60) | B3 (0.53-0.59) | B3 (0.54-0.58) |
| Red | B3 (0.63-0.69) | B3 (0.63-0.69) | B4 (0.64-0.67) | B4 (0.65-0.68) |
| Red Edge 1 | - | - | - | B5 (0.70-0.71) |
| Red Edge 2 | - | - | - | B6 (0.73-0.75) |
| Red Edge 3 | - | - | - | B7 (0.77-0.79) |
| NIR | B4 (0.76-0.90) | B4 (0.77-0.90) | B5 (0.85-0.88) | B8 (0.78-0.90) |
| NIR Narrow | - | - | - | B8A (0.86-0.88) |
| SWIR1 | B5 (1.55-1.75) | B5 (1.55-1.75) | B6 (1.57-1.65) | B11 (1.57-1.66) |
| SWIR2 | B7 (2.08-2.35) | B7 (2.09-2.35) | B7 (2.11-2.29) | B12 (2.10-2.28) |

### Escala de Reflectancia (Collection 2 L2)

Landsat C2 L2: Multiplicar por `0.0000275` y sumar `-0.2` para obtener reflectancia de superficie.

```python
def scale_landsat(image):
    optical = image.select('SR_B.').multiply(0.0000275).add(-0.2)
    return image.addBands(optical, overwrite=True)
```

## Indices Espectrales

### NDVI (Normalized Difference Vegetation Index)
Salud del manglar. Valores > 0.6 = vegetacion densa sana.

```
NDVI = (NIR - Red) / (NIR + Red)
```

| Sensor | Formula |
|--------|---------|
| Landsat 5/7 | (B4 - B3) / (B4 + B3) |
| Landsat 8/9 | (B5 - B4) / (B5 + B4) |
| Sentinel-2 | (B8 - B4) / (B8 + B4) |

### NDCI (Normalized Difference Chlorophyll Index)
Deteccion de clorofila en agua (marea roja). Solo Sentinel-2 (requiere Red Edge).

```
NDCI = (B5 - B4) / (B5 + B4)
```

Valores: < 0 = agua limpia, 0-0.1 = algas presentes, > 0.15 = bloom (marea roja)

**Limitacion**: NDCI se confunde con vegetacion terrestre (estudio Yale 2025).
Complementar siempre con FAI.

### FAI (Floating Algae Index)
Mejor separacion de blooms algales, menos ruido que NDCI.

```
FAI = B8 - (B4 + (B11 - B4) * ((832.8 - 664.6) / (1613.7 - 664.6)))
```

Solo Sentinel-2 (requiere B8, B4, B11).

### MNDWI (Modified Normalized Difference Water Index)
Mascara de agua robusta. Reemplaza el umbral fijo `B3 < 1500` del script original.

```
MNDWI = (Green - SWIR1) / (Green + SWIR1)
```

| Sensor | Formula |
|--------|---------|
| Landsat 5/7 | (B2 - B5) / (B2 + B5) |
| Landsat 8/9 | (B3 - B6) / (B3 + B6) |
| Sentinel-2 | (B3 - B11) / (B3 + B11) |

Valores: > 0 = agua, < 0 = tierra/vegetacion

### SAVI (Soil Adjusted Vegetation Index)
Complemento de NDVI para zonas con suelo expuesto (camaroneras secas).

```
SAVI = ((NIR - Red) / (NIR + Red + L)) * (1 + L)
```

Donde L = 0.5 (factor de ajuste estandar).

## Parametros de Filtrado Comunes

```python
# Sentinel-2
.filterBounds(roi)
.filterDate('YYYY-01-01', 'YYYY-12-31')
.filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 10))
.median()
.clip(roi)

# Landsat Collection 2
.filterBounds(roi)
.filterDate('YYYY-01-01', 'YYYY-12-31')
.filter(ee.Filter.lt('CLOUD_COVER', 20))
.map(scale_landsat)  # Aplicar factores de escala
.median()
.clip(roi)
```
