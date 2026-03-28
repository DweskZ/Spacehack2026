# Notas de Investigacion - Proyecto Boomerang

## Papers y Referencias Clave

### MANGLEE (Mangrove Mapping Tool)
- **Paper**: "MANGLEE: A Tool for Mapping and Monitoring MANgrove Ecosystem on Google Earth Engine" (2024)
- **Repo**: https://github.com/servir-amazonia/manglee
- **Relevancia**: Herramienta open-source validada en manglares de Guayas, Ecuador.
  Encontro >2,900 ha de manglar perdidas (2018-2022), 46% en areas protegidas.
- **Modulos**: Procesamiento de datos, Clasificacion (Random Forest), Deteccion de cambios
- **Usar**: Su metodologia de clasificacion y deteccion de cambios como referencia

### Estudio Yale - Red Tide Sentinel (2025)
- **Fuente**: Yale Center for Geospatial Solutions
- **URL**: https://geospatial.yale.edu/2025-oefs-redtidesentinel
- **Hallazgo clave**: NDCI se confunde con vegetacion terrestre ("confounded by terrestrial vegetation")
- **Recomendacion**: Usar Floating Algae Index (FAI) para mejor separacion de blooms
- **Indices probados**: NDCI, Red Tide Index, Chlorophyll Index, Floating Algae Index
- **Clasificacion**: Maximum Likelihood supervisada con composites mensuales Sentinel-2

### NASA - Shrimp Farms of the Guayas Estuary
- **URL**: https://earthobservatory.nasa.gov/images/153329/shrimp-farms-of-the-guayas-estuary
- **URL Landsat Gallery**: https://landsat.visibleearth.nasa.gov/view.php?id=153329
- **Datos clave**:
  - 1985-2014: camaroneras se duplicaron de 30,000 a 64,000 hectareas
  - 60% de piscinas estan donde antes habia manglar
  - Manglares perdieron ~20,000 ha en el mismo periodo
  - Imagen Landsat 8 del 29 agosto 2024 documenta la zona
- **Programa Socio Bosque**: Gobierno Ecuador 2008, incentivos para conservacion forestal
- **Programa Socio Manglar**: Extension especifica para proteccion de manglares

### Land Use Change Gulf of Guayaquil (2025)
- **Paper**: "Land use change and mangrove conservation strategies in the Gulf of Guayaquil"
- **Fuente**: Springer Nature (Discover Applied Sciences)
- **Datos**: 2000-2022, acuicultura expandio 20,610 ha. Manglar aumento 6,111 ha cerca de zonas protegidas.

## Herramientas Descubiertas

### geemap (Seleccionada para el prototipo)
- **Repo**: https://github.com/gee-community/geemap
- **Version**: v0.37+ (marzo 2026)
- **Que es**: Libreria Python para GEE interactivo en Jupyter
- **Por que**: Replica la experiencia del editor web GEE pero en Python
- **Instalacion**: `pip install geemap`
- **Feature util**: `geemap.js_to_python()` convierte codigo JS de GEE a Python
- **Docs**: https://geemap.org / https://geemap.readthedocs.io

### Earth Agent MCP
- **Repo**: https://github.com/wybert/earth-agent-chrome-ext
- **Que es**: Agente IA que controla GEE, integrable con Cursor via MCP
- **NPM**: `earth-agent-mcp` v1.3.0
- **Uso potencial**: Asistente dentro de Cursor para generar codigo GEE
- **Estado**: Activo, 98 stars, enero 2026

### GeoAgent
- **Repo**: https://github.com/opengeos/GeoAgent
- **Que es**: Agente IA con pipeline de 4 agentes (Planner, Data, Analysis, Visualization)
- **Instalacion**: `pip install geoagent`
- **Estado**: Nuevo (febrero 2026), por el creador de geemap

### OpenEarthAgent
- **Repo**: https://github.com/mbzuai-oryx/OpenEarthAgent
- **Que es**: Framework academico para agentes geoespaciales con tool-augmented reasoning
- **Estado**: Paper febrero 2026, mas orientado a investigacion que a produccion

## Datos Historicos de la Zona

- **1970s**: Inicio de acuicultura en sur de Ecuador
- **1985**: ~30,000 ha de camaroneras en Estuario del Guayas
- **2008**: Programa Socio Bosque (incentivos conservacion)
- **2014**: ~64,000 ha de camaroneras (se duplicaron en 29 anios)
- **2018-2022**: >2,900 ha de manglar perdidas (MANGLEE)
- **2023**: Ecuador exporto ~$7.6 mil millones en camaron
- **60%** de piscinas camaroneras ocupan terreno de ex-manglar

## Hallazgos Tecnicos

1. **NDCI tiene limitaciones**: Se confunde con vegetacion terrestre, FAI es mejor para blooms
2. **Collection 2 armonizada**: No se requiere correccion extra entre Landsat 5/7/8/9
3. **Landsat 7 SLC-off**: Desde 2003 tiene striping, usar como complemento no como fuente primaria
4. **Sentinel-2 Red Edge**: Bandas B5-B7 son unicas de S2, permiten NDCI que Landsat no puede
5. **MNDWI > umbral fijo**: Para mascara de agua, MNDWI es mas robusto que `B3 < 1500`
6. **Random Forest 50 arboles**: Buen balance precision/velocidad para clasificacion en GEE

## Valor Economico de Referencia

- Servicios ecosistemicos del manglar: $33,000-57,000 USD/ha/anio
- Costo restauracion de manglar: ~$5,000-15,000 USD/ha
- Mortalidad de camaron por hipoxia: 30-80% de produccion por evento
- Exportaciones de camaron Ecuador 2023: ~$7.6 mil millones
