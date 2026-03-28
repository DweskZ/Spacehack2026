# Boomerang — Propuesta de solución (Track MCC, SpaceHACK 2026)

Documento de apoyo para rellenar el **Final Submission Template** (8 diapositivas). Los jueces puntúan sobre todo el **.pptx**; este texto sirve para copiar titulares y asegurar coherencia con el prototipo en código.

**Convención de nombres (PDF del hackathon):** `MCC_<Team#>_00_SLIDES.pptx` y, si adjuntáis material extra, `MCC_<Team#>_01_Prototype.zip` (ejemplo).

---

## 1. Introducción (1–3 slides)

**Problema (track):** Greater Guayaquil concentra vulnerabilidad hídrica (ríos + marea + lluvia extrema) y pérdida de barrera de manglar; el planeamiento urbano no integra bien el rol protector del ecosistema.

**Enfoque del equipo:** No entregar solo mapas estáticos, sino un **Boomerang Alert Engine** — flujo trazable que une:

1. **Earth Observation** en Google Earth Engine (Sentinel-2 mediana, Landsat 8 para cambio 2013–2024, índices NDVI / MNDWI / NDBI, franja costera 500 m) + **Global Mangrove Watch 2020** (`projects/sat-io/.../GMW_MNG_2020`, ~10 m) como capa de validación frente a la clasificación por umbrales.
2. **Pronóstico meteorológico abierto** (Open-Meteo **Forecast** + **Marine** para nivel del mar / proxy de marea, sin API key) para ventanas de 72 h / 7 días.
3. **Reglas de severidad** alineadas al enunciado (>70 mm/día como referencia de lluvia extrema) + **coincidencia lluvia + nivel del mar alto** + **proxy económico** de exposición (orden de magnitud, explícitamente no actuarial).

**Resumen ejecutivo (viñetas para slide):**

- Resultados iniciales: porcentaje de costa “protegida vs expuesta”, simulación de crecida costera sin manglar cercano, tendencias NDCI / manglar.
- Oportunidades: capas **SERVIR / MANGLEE (Guayas)** como raster adicional, tablas de marea **INOCAR** (sustituir proxy Marine en estuario), y modelo hidráulico con DEM para política pública.
- Lecciones: la clasificación por umbrales es rápida para el hackathon; un modelo ML espacial (Random Forest como en MANGLEE) mejoraría generalización; el proxy económico debe validarse con datos locales.

---

## 2. Mapas / visualización (2–5 slides)

**Qué mostrar (cada figura con pie de fuente):**

| Producto | Fuente de datos |
|----------|-----------------|
| RGB y clasificación de uso de suelo | COPERNICUS/S2_SR_HARMONIZED (GEE) |
| Manglar de referencia (validación) | **GMW 2020** `projects/sat-io/open-datasets/GMW/annual-extent/GMW_MNG_2020` (Bunting et al., 2022; CC BY 4.0) |
| Costa protegida / expuesta (500 m) | Misma imagen + máscara agua + buffer morfológico |
| Cambio urbano vs pérdida de manglar | LANDSAT/LC08/C02/T1_L2 |
| Simulación “marea” | Proxy morfológico desde máscara de agua (no batimetría) |
| Centro de alertas | Open-Meteo Forecast + Marine + métricas EO (`boomerang_alerts.py`) |

**Key takeaway (caja en slide):** El satélite permite **monitorizar** manglar (y **contrastar** con GMW) y exposición costera; la meteorología marina + lluvia permite **anticipar** ventanas de riesgo compuesto — prototipo de **conciencia y priorización**, no un reemplazo de ECU911/INOCAR.

---

## 3. Análisis / correlación cuantitativa (1–4 slides)

**Preguntas del track cubiertas (parcialmente) en el prototipo:**

- % de costa con / sin protección de manglar → capa costera + estadísticas de área.
- Pérdida / dinámica de manglar en ~10 años → Landsat urbano vs manglar (notebook 07).
- Zonas expuestas y orden de magnitud económico → proxy USD en motor de alertas (con disclaimer).
- Salud del manglar (NDVI) y agua (NDCI) → tendencias en dashboard.

**Referencias bibliográficas sugeridas en slide:** Global Mangrove Watch (Bunting et al.); informes de EcoCiencia/SERVIR; documentos citados en `officialtrack.md`.

---

## 4. Impacto / “¿para qué el satélite?” (1–2 slides)

**So what:** La observación terrestre hace **visible y comparable** la franja costera a la escala de millones de habitantes; el pronóstico añade **temporalidad**. Juntos habilitan **alertas comunitarias** y **priorización de restauración** — vínculo directo con ODS (ciudades resilientes, vida bajo el agua, clima).

**Política pública:** El prototipo muestra cómo un municipio podría integrar un tablero EO + meteorología en educación ambiental y planes de uso de suelo costero; la validación local y los datos hidráulicos son el siguiente escalón.

---

## 5. Agradecimiento (1 slide)

Equipo, institución, agradecimiento a organizadores y a comunidad de datos abiertos (GEE, Open-Meteo).

---

## Qué entregar como “no es solo un dashboard”

1. **Motor de alertas** (`boomerang_alerts.py` + pestaña **Centro de Alertas**): cola priorizada, lluvia + nivel del mar + costa EO, severidades, acciones, fuentes citadas.
2. **Datos en vivo** (Open-Meteo Forecast + Marine) + **umbrales** del problem statement + alerta **lluvia + marea relativa**.
3. **GMW 2020** en mapas (`gee_layers.py` + dashboard) para validación frente a clasificación S2.
4. **Proxy económico explícito** (con limitaciones escritas).
5. **Hoja de ruta**: INOCAR (marea náutica), MANGLEE/SERVIR en GEE si se requiere cobertura Guayas adicional, modelo inundación DEM.

Esto responde al template pidiendo **correlación cuantitativa** e **impacto**, y diferencia el entregable de un visor pasivo.
