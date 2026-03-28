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
