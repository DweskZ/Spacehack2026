"""
Capas de referencia en Google Earth Engine (validación vs clasificación por umbrales).

Global Mangrove Watch (GMW) 2020 v4 — resolución ~10 m, CC BY 4.0.
Bunting et al., 2022 (v3 serie); extensión anual 2020 en projects/sat-io/...
"""

from __future__ import annotations

import ee

# GMW Sentinel baseline 2020 (10 m), sat-io catalog
GMW_MNG_2020_IC = "projects/sat-io/open-datasets/GMW/annual-extent/GMW_MNG_2020"


def gmw_mangrove_mask_2020(roi: ee.Geometry) -> ee.Image:
    """Máscara binaria 1 = manglar según GMW 2020 en el ROI."""
    gmw = ee.ImageCollection(GMW_MNG_2020_IC).filterBounds(roi).mosaic().clip(roi)
    return gmw.eq(1).selfMask().rename("gmw_mangrove")


def gmw_union_mangrove_v3(roi: ee.Geometry) -> ee.Image:
    """Unión temporal 1996–2020 (v3, ~25 m) — píxeles que fueron manglar en algún epoch."""
    u = ee.Image(
        "projects/earthengine-legacy/assets/projects/sat-io/open-datasets/GMW/union/gmw_v3_mng_union"
    )
    return u.clip(roi).gt(0).selfMask().rename("gmw_union")


def classify_landcover_from_s2(s2: ee.Image, roi: ee.Geometry) -> ee.Image:
    """
    Clases 1–4: manglar, urbano, agua, suelo (resto).

    Manglar ya no es solo «NDVI alto» (eso mezclaba bosque seco denso). Se usa:
    - **GMW 2020** como ancla donde exista producto;
    - **firma espectral** NDVI + MNDWI típica de vegetación húmeda costera (excluye NDVI muy alto + MNDWI muy bajo = bosque seco).

    Prioridad de pintado: agua → urbano → manglar → suelo.
    """
    ndvi = s2.normalizedDifference(["B8", "B4"])
    mndwi = s2.normalizedDifference(["B3", "B11"])
    ndbi = s2.normalizedDifference(["B11", "B8"])

    gmw_m = gmw_mangrove_mask_2020(roi).eq(1)

    agua = mndwi.gt(0.17).And(ndvi.lt(0.40))
    urbano = (
        ndbi.gt(0.02)
        .And(ndvi.lt(0.34))
        .And(ndvi.gt(0.0))
        .And(agua.Not())
    )
    # Vegetación húmeda / intermareal (manglar) sin confundir con bosque seco NDVI alto pero seco
    spectral_manglar = (
        ndvi.gte(0.36)
        .And(ndvi.lt(0.72))
        .And(mndwi.gt(-0.11))
        .And(mndwi.lt(0.33))
        .And(ndvi.gt(0.54).And(mndwi.lt(-0.07)).Not())
    )
    manglar = gmw_m.Or(spectral_manglar).And(agua.Not()).And(urbano.Not())

    classified = ee.Image(4)
    classified = classified.where(agua, 3)
    classified = classified.where(urbano.And(agua.Not()), 2)
    classified = classified.where(manglar.And(agua.Not()).And(urbano.Not()), 1)
    return classified.clip(roi)


def extra_categorical_masks(
    s2: ee.Image, classified: ee.Image, roi: ee.Geometry
) -> dict[str, ee.Image]:
    """
    Capas categóricas extra (proxies heurísticos para demo; validar con campo / MANGLEE).

    - bosque_seco: vegetación seca (MNDWI bajo), explícitamente **sin** GMW ni clase manglar del mapa base.
    - industrial: NDBI alto y cobertura baja/med (tejido construido / mixto).
    - contaminacion_agua: píxeles agua con NDCI elevado (proxy clorofila / materia en agua).
    """
    ndvi = s2.normalizedDifference(["B8", "B4"])
    mndwi = s2.normalizedDifference(["B3", "B11"])
    ndbi = s2.normalizedDifference(["B11", "B8"])
    ndci = s2.normalizedDifference(["B5", "B4"])

    gmw_m = gmw_mangrove_mask_2020(roi).eq(1)
    manglar = classified.eq(1)
    urbano = classified.eq(2)
    agua = classified.eq(3)

    bosque_seco = (
        ndvi.gte(0.26)
        .And(ndvi.lt(0.58))
        .And(mndwi.lt(-0.08))
        .And(gmw_m.Not())
        .And(manglar.Not())
        .And(urbano.Not())
        .And(agua.Not())
    )
    industrial = (
        ndbi.gt(0.10)
        .And(ndvi.lt(0.32))
        .And(ndvi.gt(0.02))
        .And(agua.Not())
    )
    contaminacion_agua = agua.And(ndci.gt(0.12))

    return {
        "bosque_seco": bosque_seco.selfMask().rename("bosque_seco"),
        "industrial": industrial.selfMask().rename("industrial"),
        "contaminacion_agua": contaminacion_agua.selfMask().rename("contaminacion"),
    }


def _s2_classified_median(roi: ee.Geometry) -> tuple[ee.Image, ee.Image]:
    s2 = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(roi)
        .filterDate("2023-06-01", "2024-12-31")
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 40))
        .median()
        .clip(roi)
    )
    classified = classify_landcover_from_s2(s2, roi)
    return s2, classified


def inundacion_buffer_meters(tide_pct: float, rain_stress_pct: float) -> int:
    """
    Buffer desde la máscara de agua (m), alineado al tablero (máx. ~830 m).
    Base mínima 80 m: con ~30 m el proxy casi no alcanzaba tejido urbano en S2 y las tablas salían en cero.
    """
    t = float(tide_pct) / 100.0
    r = float(rain_stress_pct) / 100.0
    return int(80 + t * 600 + r * 150)


def inundacion_mask(
    classified: ee.Image,
    tide_pct: float,
    rain_stress_pct: float = 0.0,
) -> ee.Image:
    """
    Proxy de inundación costera: expansión desde agua + estrés de lluvia (scroll).
    rain_stress_pct 0–100 suma hasta ~150 m extra de buffer (metáfora lluvia + crecida).
    """
    water = classified.eq(3)
    buffer_m = inundacion_buffer_meters(tide_pct, rain_stress_pct)
    expanded = water.focal_max(radius=buffer_m, units="meters")
    mang_near = classified.eq(1).focal_max(radius=280, units="meters")
    return (
        expanded.And(classified.eq(2).Or(classified.eq(4)))
        .And(mang_near.Not())
        .selfMask()
        .rename("inundacion")
    )


def compute_flood_proxy_stats(
    roi: ee.Geometry,
    tide_pct: float = 50.0,
    rain_stress_pct: float = 0.0,
    scale_m: int = 100,
) -> dict[str, float]:
    """
    % de área del ROI bajo inundación proxy (misma lógica que simulación marea + estrés lluvia).
    % del suelo urbano+suelo (clases 2+4) expuesto a inundación sin manglar cercano.
    """
    s2, classified = _s2_classified_median(roi)

    flood = inundacion_mask(classified, tide_pct, rain_stress_pct)

    pa = ee.Image.pixelArea()
    roi_area_m2 = roi.area(maxError=1)
    flood_sum = flood.multiply(pa).reduceRegion(
        ee.Reducer.sum(), roi, scale=scale_m, maxPixels=1e13
    )
    urban = classified.eq(2).Or(classified.eq(4))
    urban_sum = urban.multiply(pa).reduceRegion(
        ee.Reducer.sum(), roi, scale=scale_m, maxPixels=1e13
    )

    flood_info = flood_sum.getInfo()
    urban_info = urban_sum.getInfo()
    roi_m2 = roi_area_m2.getInfo()

    f_m2 = float(list(flood_info.values())[0] or 0)
    u_m2 = float(list(urban_info.values())[0] or 0)
    pct_roi = 100.0 * f_m2 / roi_m2 if roi_m2 else 0.0
    pct_urban = 100.0 * f_m2 / u_m2 if u_m2 else 0.0

    return {
        "pct_roi_inundacion_proxy": round(pct_roi, 3),
        "pct_urbano_suelo_inundacion_proxy": round(pct_urban, 3),
        "ha_inundacion_proxy": round(f_m2 / 10000.0, 1),
        "tide_pct_usado": tide_pct,
        "rain_stress_pct_usado": rain_stress_pct,
    }


# Rectángulos [minLon, minLat, maxLon, maxLat] — barrios aproximados para demo / comunicación.
# No son límites catastrales ni jurisdicción oficial; el ROI recorta al área del tablero.
GUAYAQUIL_DEMO_ZONES: list[tuple[str, list[float]]] = [
    ("Mapasingue / Flor de Bastión", [-80.10, -2.14, -79.98, -2.00]),
    ("Sauces / Alborada (norte)", [-79.98, -2.10, -79.88, -1.98]),
    ("Samborondón / vía a la costa", [-79.96, -2.08, -79.85, -1.98]),
    ("Centro / Olmedo", [-80.02, -2.18, -79.92, -2.10]),
    ("Urdesa / Kennedy", [-79.95, -2.18, -79.88, -2.10]),
    ("Puerto Santa Ana / Malecón 2000", [-79.95, -2.22, -79.88, -2.14]),
    ("Estero Salado (oeste)", [-80.10, -2.24, -79.99, -2.14]),
    ("Guasmo", [-80.05, -2.24, -79.98, -2.16]),
    ("Cristo del Consuelo", [-79.99, -2.28, -79.90, -2.22]),
    ("Suburbio / Febres Cordero", [-80.05, -2.30, -79.92, -2.24]),
    ("Trinidad de Dios / Sur", [-79.98, -2.28, -79.90, -2.22]),
    ("Sur / Guasmo sur", [-80.02, -2.28, -79.98, -2.22]),
    ("Orillas estero (norte)", [-80.00, -2.14, -79.98, -2.10]),
    ("Este del estuario (límite ROI)", [-79.90, -2.22, -79.85, -2.08]),
]


def demo_zone_names() -> list[str]:
    """Nombres de zona en el mismo orden que `named_zones_guayaquil` (para UI)."""
    return [name for name, _ in GUAYAQUIL_DEMO_ZONES]


def named_zones_guayaquil(roi: ee.Geometry) -> ee.FeatureCollection:
    """
    Rectángulos aproximados dentro del ROI (Greater Guayaquil / estuario).
    Nombres para comunicación; no son límites administrativos oficiales.
    """
    feats = []
    for nombre, box in GUAYAQUIL_DEMO_ZONES:
        g = ee.Geometry.Rectangle(box).intersection(roi, ee.ErrorMargin(1))
        feats.append(ee.Feature(g, {"nombre": nombre}))
    return ee.FeatureCollection(feats)


def zone_inundacion_ranking(
    roi: ee.Geometry,
    tide_pct: float,
    rain_stress_pct: float = 0.0,
    scale_m: int = 100,
) -> list[dict[str, float | str]]:
    """Por zona: ha bajo inundación proxy y % respecto al área de la zona."""
    s2, classified = _s2_classified_median(roi)
    flood = inundacion_mask(classified, tide_pct, rain_stress_pct)
    zones = named_zones_guayaquil(roi)

    flood_ha = flood.rename("f").multiply(ee.Image.pixelArea()).divide(10000)
    zona_ha = ee.Image(1).multiply(ee.Image.pixelArea()).divide(10000).rename("a")
    stack = flood_ha.addBands(zona_ha)

    stats = stack.reduceRegions(
        collection=zones,
        reducer=ee.Reducer.sum(),
        scale=scale_m,
        tileScale=2,
    )

    rows: list[dict[str, float | str]] = []
    for feat in stats.getInfo().get("features", []):
        props = feat.get("properties") or {}
        nombre = props.get("nombre", "?")
        f_ha = float(props.get("f", 0) or 0)
        a_ha = float(props.get("a", 0) or 0)
        pct = 100.0 * f_ha / a_ha if a_ha > 1e-6 else 0.0
        rows.append(
            {
                "Zona": nombre,
                "ha_inundacion_proxy": round(f_ha, 1),
                "pct_zona_inundada": round(pct, 2),
                "ha_zona_total": round(a_ha, 1),
            }
        )
    rows.sort(key=lambda r: float(r["ha_inundacion_proxy"]), reverse=True)
    return rows


def _geometry_ok_for_leaflet(geom: dict | None) -> bool:
    """Evita features sin geometría o vacías: Leaflet puede dejar el mapa en blanco."""
    if not geom or not geom.get("type"):
        return False
    t = geom["type"]
    if t == "Polygon":
        coords = geom.get("coordinates")
        if not coords or not coords[0] or len(coords[0]) < 4:
            return False
        return True
    if t == "MultiPolygon":
        coords = geom.get("coordinates")
        return bool(coords and len(coords) > 0 and coords[0] and coords[0][0])
    return False


def zones_geojson_for_map(roi: ee.Geometry) -> dict:
    """GeoJSON para Folium (límites de zonas con nombre). Solo features con geometría válida."""
    raw = named_zones_guayaquil(roi).getInfo()
    feats = []
    for f in raw.get("features", []):
        g = f.get("geometry")
        if _geometry_ok_for_leaflet(g):
            feats.append(f)
    return {"type": "FeatureCollection", "features": feats}
