# Proyecto Boomerang - SpaceHACK 2026

## Hipotesis

La deforestacion de manglares para expandir camaroneras elimina filtros biologicos
naturales, facilitando eventos de Marea Roja. Esto genera un "efecto boomerang":
la marea roja provoca hipoxia (caida de oxigeno) en las mismas piscinas que
reemplazaron al manglar, destruyendo la produccion.

> Es como que un humano consuma coca cola toda la vida y se pregunte
> por que le fallan los rinones despues.

## Equipo

- **Luis Emilio Figueroa Arteaga**: Software Engineering Student (6to Semestre, ULEAM)
  - Lider del Club de IA
  - Role: Data Analysis & Lead Software Developer
  - Institution: Universidad Laica Eloy Alfaro de Manabi (ULEAM)

## Google Earth Engine Project

- **Nombre**: My Project 29952
- **Cloud Project ID**: `august-tower-470819-s6`
- **Inicializar con**: `ee.Initialize(project='august-tower-470819-s6')`

## Region de Interes (ROI)

Golfo de Guayaquil, Ecuador - zona de estuario con manglares y camaroneras intensivas.

```
type: Polygon
coordinates:
  [[-80.23433322304784, -2.581044464678974],
   [-80.09837741250097, -2.581044464678974],
   [-80.09837741250097, -2.4212085241315013],
   [-80.23433322304784, -2.4212085241315013],
   [-80.23433322304784, -2.581044464678974]]
```

Esquina SW: [-80.2343, -2.5810]
Esquina NE: [-80.0984, -2.4212]

## Solucion Propuesta: Boomerang Risk Index (BRI)

Indice compuesto que combina 3 capas satelitales:

1. **Manglar perdido**: Cambio temporal de NDVI (Landsat 2008-2024)
2. **Expansion camaronera**: Clasificacion supervisada (Random Forest) con Sentinel-2
3. **Riesgo de marea roja**: NDCI + Floating Algae Index (FAI) sobre masas de agua

Formula: `BRI = w1 * MangroveChange + w2 * ShrimpExpansion + w3 * RedTideRisk`

Zonas con BRI alto = "el boomerang ya viene de regreso"

**Alerta temprana**: Cuando el NDCI en aguas adyacentes a camaroneras sin manglar
supera un umbral, se genera alerta de riesgo de hipoxia.

## Feedback del Q&A (Mentor)

> "No solo menciones los beneficios, sino tambien mide el impacto cuantitativamente.
> Con eso al jurado le queda clarisimo las metricas que definen exito de tu proyecto.
> El impacto debe ser medido con metricas que respalden la viabilidad de la propuesta."

### Metricas clave a calcular:
- Hectareas de manglar perdidas (2008-2024)
- Tasa de expansion camaronera (ha/anio)
- Correlacion Pearson entre perdida de manglar y aumento de NDCI
- Overall Accuracy y Kappa del clasificador
- Valor economico en riesgo (servicios ecosistemicos del manglar)

## Formato de Entrega SpaceHACK

- Slide Deck: `MCC_19_00_SLIDES.pptx`
- Codigo/Soporte: `MCC_19_01_Code.zip`
- Estructura de slides:
  - Introduction (1-3): Team, Approach, Executive Summary
  - Maps/Data Vis (2-5): Screenshots GEE (NDVI, NDCI, Clasificacion)
  - Analysis (1-4): Correlacion cuantitativa manglar-marea roja
  - Impact (1-2): "So What?" y alineacion UN SDGs

## UN SDGs Alineados

- SDG 14: Life Below Water
- SDG 15: Life on Land
- SDG 13: Climate Action
- SDG 12: Responsible Consumption and Production
