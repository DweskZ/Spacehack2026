import json
import math
import os

import ee
import altair as alt
import folium
from folium import MacroElement
from folium.template import Template
import pandas as pd
import streamlit as st
from datetime import datetime
from streamlit_folium import st_folium

from boomerang_alerts import (
    SEVERITY_LABEL,
    alerts_to_dataframe_rows,
    build_alerts,
    fetch_marine_sea_level_hourly,
    fetch_open_meteo_archive_precipitation,
    fetch_open_meteo_precip_forecast,
    forecast_vs_history_context,
    peak_precipitation_day_72h,
)
from gee_layers import (
    _s2_classified_median,
    classify_landcover_from_s2,
    compute_flood_proxy_stats,
    demo_zone_names,
    extra_categorical_masks,
    gmw_mangrove_mask_2020,
    inundacion_buffer_meters,
    inundacion_mask,
    zone_inundacion_ranking,
    zones_geojson_for_map,
)
from zone_notifications_demo import (
    citizen_flood_cards_pair,
    format_day_en,
    human_depth_phrase_cm,
    water_depth_cm_from_forecast_mm,
)


def format_day_es(iso_date: str | None) -> str:
    """Etiqueta corta en español: «28 Mar»."""
    if not iso_date:
        return "—"
    try:
        from datetime import datetime as _dt
        d = _dt.strptime(str(iso_date)[:10], "%Y-%m-%d")
    except ValueError:
        return str(iso_date)
    meses = ("Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic")
    return f"{d.day} {meses[d.month - 1]}"

st.set_page_config(page_title="Boomerang — Greater Guayaquil", layout="wide", page_icon="🌊")

st.markdown(
    """
    <style>
        /* Mapa a casi todo el ancho, centrado (layout wide + menos márgenes laterales) */
        .main .block-container {
            padding-top: 1.2rem;
            max-width: min(1920px, 100%);
            padding-left: clamp(0.75rem, 2vw, 1.5rem);
            padding-right: clamp(0.75rem, 2vw, 1.5rem);
        }
        h1 { letter-spacing: -0.02em; }
        /* Sidebar alineada a tema oscuro (evita franja blanca con “Dark” del navegador/Streamlit) */
        [data-testid="stSidebar"] {
            background: linear-gradient(185deg, #1a1d24 0%, #0e1117 100%) !important;
            border-right: 1px solid rgba(255, 255, 255, 0.08);
        }
        [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
        [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h3 {
            color: #e6edf3 !important;
        }
        [data-testid="stSidebar"] .stCaption { color: #9da7b3 !important; }
    </style>
    """,
    unsafe_allow_html=True,
)

@st.cache_resource
def init_ee():
    import pathlib
    project = os.environ.get("EE_PROJECT_ID", "august-tower-470819-s6")
    # Sin secrets.toml, cualquier acceso a st.secrets (in, .get, etc.) dispara _parse() y
    # StreamlitSecretNotFoundError. Solo leer ee_token si existe el archivo.
    ee_token = None
    if st.secrets.load_if_toml_exists():
        ee_token = st.secrets.get("ee_token")
    if ee_token:
        creds_dir = pathlib.Path.home() / ".config" / "earthengine"
        creds_dir.mkdir(parents=True, exist_ok=True)
        with open(creds_dir / "credentials", "w") as f:
            json.dump(dict(ee_token), f)
    ee.Initialize(project=project)


init_ee()

roi = ee.Geometry.Rectangle([-80.10, -2.30, -79.85, -1.98])
# Folium: [[south, west], [north, east]] — mismo rectángulo que clip en GEE
ROI_FIT_BOUNDS = [[-2.30, -80.10], [-1.98, -79.85]]
ROI_CENTER = [
    (ROI_FIT_BOUNDS[0][0] + ROI_FIT_BOUNDS[1][0]) / 2,
    (ROI_FIT_BOUNDS[0][1] + ROI_FIT_BOUNDS[1][1]) / 2,
]

# streamlit-folium no aplica bien fit_bounds del HTML; hay que pasar zoom/center al componente.
# Con poco alto en px, el zoom máximo que encaja la latitud del ROI es ~11 → rectángulo “pequeño”.
# ~940 px de alto permite zoom 12 sin recortar el ROI en vertical (Web Mercator aprox.).
MAIN_MAP_HEIGHT_PX = 940
# Ancho de referencia para calcular zoom/center (iframe suele ser ~ancho del contenedor en layout wide).
MAIN_MAP_WIDTH_REF_PX = 1500.0

# Mapa base por defecto: imágenes satélite (Esri World Imagery). Sin API key; atribución en `attr`.
DEFAULT_BASEMAP_TILES = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
)
DEFAULT_BASEMAP_ATTR = "Esri — World Imagery"


def _zoom_center_for_roi_panel(
    width_px: float,
    height_px: float,
) -> tuple[tuple[float, float], int]:
    """
    Zoom y centro para que el bbox del ROI llene el panel (misma idea que Leaflet fitBounds).
    Devuelve zoom entero y centro (lat, lon).
    """
    south, west = ROI_FIT_BOUNDS[0]
    north, east = ROI_FIT_BOUNDS[1]
    lat_c = (south + north) / 2.0
    lon_c = (west + east) / 2.0

    def lat_y(lat_deg: float) -> float:
        # Proyección esférica Web Mercator (Y en fracción de mundo 0..1)
        s = math.sin(math.radians(lat_deg))
        return 0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)

    y_min = lat_y(min(south, north))
    y_max = lat_y(max(south, north))
    y_frac = abs(y_max - y_min)

    x_min = (west + 180.0) / 360.0
    x_max = (east + 180.0) / 360.0
    x_frac = abs(x_max - x_min)
    if x_frac > 0.5:
        x_frac = 1.0 - x_frac

    WORLD_DIM = 256.0
    ZOOM_MAX = 18
    best_z = 10
    for z in range(ZOOM_MAX, 4, -1):
        scale = 2**z
        px_per_world_y = height_px / (y_frac * WORLD_DIM * scale) if y_frac > 1e-9 else float("inf")
        px_per_world_x = width_px / (x_frac * WORLD_DIM * scale) if x_frac > 1e-9 else float("inf")
        if px_per_world_y >= 1.0 and px_per_world_x >= 1.0:
            best_z = z
            break
    return (lat_c, lon_c), best_z


# Flags de capas (un mapa; visibilidad en el iframe vía JS — sin rerun de Streamlit).
LAYER_FLAG_LABELS: dict[str, str] = {
    "rgb": "Sentinel-2 RGB",
    "classified": "S2 land cover (thresholds)",
    "ndvi": "NDVI",
    "gmw": "GMW 2020 mangrove",
    "prot": "Protected (coastal strip)",
    "exp": "Exposed (urban)",
    "vuln": "Vulnerable",
    "marea": "Water spread (tide proxy)",
    "inun": "Flood proxy",
    "bosque": "Dry forest (proxy)",
    "ind": "Industrial (proxy)",
    "cont": "High NDCI water (proxy)",
    "zones": "Neighbourhoods (demo)",
}
# Texto en el panel del mapa: nombre corto + ayuda (atributo title) para el cliente final.
LAYER_PANEL_UI: dict[str, dict[str, str]] = {
    "classified": {
        "label": "Land use",
        "help": "Green = vegetation/mangrove, red = built-up, blue = water, gold = soil/farmland. See legend.",
    },
    "ndvi": {
        "label": "Greenness (plants)",
        "help": "Red to green: sparse to dense cover. NDVI is a technical index, not a crop map.",
    },
    "gmw": {
        "label": "Mangrove (global map)",
        "help": "Where GMW mapped mangrove in 2020. Reference layer, not an official inventory.",
    },
    "prot": {
        "label": "Vegetated coast",
        "help": "Strip near the water with plant cover (more buffered against surge).",
    },
    "exp": {
        "label": "City facing the water",
        "help": "Built-up coast with less natural mangrove buffer.",
    },
    "vuln": {
        "label": "Medium-risk coast",
        "help": "Between protected and highly exposed (illustrative).",
    },
    "marea": {
        "label": "Rising water (simulation)",
        "help": "Blue: wider water if tide rises in this test. Not a real tide forecast.",
    },
    "inun": {
        "label": "Flooding (simulation)",
        "help": "Red: model flood mask for the chosen scenario. Not a substitute for official hazard maps.",
    },
    "bosque": {
        "label": "Dry forest",
        "help": "Drier vegetation (not mangrove). Approximate indicator.",
    },
    "ind": {
        "label": "Industrial area",
        "help": "Built or industrial fabric (satellite estimate).",
    },
    "cont": {
        "label": "Water quality (indicator)",
        "help": "Purple tones: more material in the water (visual proxy, not lab data).",
    },
    "zones": {
        "label": "Neighbourhoods (demo)",
        "help": "Rough boxes for demo alerts; not official boundaries.",
    },
}

# Leyendas por capa: solo se muestran en el panel si esa capa está activa (layer_ids = boom_id).
MAP_LEGEND_GROUPS: list[dict] = [
    {
        "layer_ids": ["classified"],
        "title": "Land use — what each colour means",
        "rows": [
            {"hex": "#228B22", "text": "Green: wet vegetation / mangrove (estimate)"},
            {"hex": "#FF4500", "text": "Red: urban or built-up"},
            {"hex": "#4169E1", "text": "Blue: open water"},
            {"hex": "#DAA520", "text": "Gold: bare soil or farmland"},
        ],
    },
    {
        "layer_ids": ["ndvi"],
        "title": "Greenness (low to high)",
        "rows": [
            {"hex": "#FF0000", "text": "Red: sparse vegetation"},
            {"hex": "#FFFF00", "text": "Yellow: medium cover"},
            {"hex": "#006400", "text": "Dark green: dense vegetation"},
        ],
    },
    {
        "layer_ids": ["gmw"],
        "title": "Mangrove (global 2020 reference)",
        "rows": [
            {"hex": "#00FF88", "text": "Light green: mangrove per GMW (science reference)"},
        ],
    },
    {
        "layer_ids": ["prot"],
        "title": "Vegetated coast",
        "rows": [
            {"hex": "#00FF00", "text": "Green: coast with more plant cover"},
        ],
    },
    {
        "layer_ids": ["exp"],
        "title": "City facing the water",
        "rows": [
            {"hex": "#FF0000", "text": "Red: urban very close to water"},
        ],
    },
    {
        "layer_ids": ["vuln"],
        "title": "Medium-risk coast",
        "rows": [
            {"hex": "#FFA500", "text": "Orange: in-between situation"},
        ],
    },
    {
        "layer_ids": ["marea"],
        "title": "Rising water (simulation)",
        "rows": [
            {"hex": "#1E90FF", "text": "Blue: wider water with the simulated tide"},
        ],
    },
    {
        "layer_ids": ["inun"],
        "title": "Flooding (simulation)",
        "rows": [
            {"hex": "#DC143C", "text": "Red: flood proxy for the chosen scenario"},
        ],
    },
    {
        "layer_ids": ["bosque"],
        "title": "Dry forest",
        "rows": [
            {"hex": "#CD853F", "text": "Brown: dry vegetation (not mangrove)"},
        ],
    },
    {
        "layer_ids": ["ind"],
        "title": "Industrial zone",
        "rows": [
            {"hex": "#708090", "text": "Grey: industrial or mixed fabric"},
        ],
    },
    {
        "layer_ids": ["cont"],
        "title": "Water quality (indicator)",
        "rows": [
            {"hex": "#DA70D6", "text": "Purple: more material in the water (visual proxy)"},
        ],
    },
    {
        "layer_ids": ["zones"],
        "title": "Neighbourhoods (demo)",
        "rows": [
            {"hex": "#3388ff", "text": "Outline: approximate area (pick the name in the selector below the map)"},
        ],
    },
]

OVERLAY_FLAG_KEYS = [k for k in LAYER_FLAG_LABELS if k != "rgb"]

# Rejilla de escenarios precalculados (marea × lluvia) para cambiar teselas en el iframe sin nueva petición GEE.
SCENARIO_GRID_VALUES = [0, 25, 50, 75, 100]

# Etiquetas cualitativas (misma rejilla 0–100; proxy morfológico, no mm ni marea oficial INOCAR).
TIDE_SCENARIO_LABELS = [
    "Low tide — little spread from water",
    "Low to mid tide",
    "Mid tide",
    "High tide",
    "High water / spring — maximum spread",
]
RAIN_SCENARIO_LABELS = [
    "No heavy showers / isolated drizzle",
    "Light rain (~5–15 mm/day)",
    "Moderate rain (~15–40 mm/day)",
    "Heavy rain (~40–80 mm/day)",
    "Very heavy rain / local storm (≥ ~80 mm/day)",
]


@st.cache_resource
def get_tile_url(_image, vis_params):
    map_id = _image.getMapId(vis_params)
    return map_id['tile_fetcher'].url_format

@st.cache_resource
def load_tiles():
    s2 = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
        .filterBounds(roi).filterDate('2023-06-01', '2024-12-31')
        .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 40))
        .median().clip(roi))

    ndvi = s2.normalizedDifference(['B8', 'B4']).rename('NDVI')
    mndwi = s2.normalizedDifference(['B3', 'B11']).rename('MNDWI')
    ndbi = s2.normalizedDifference(['B11', 'B8']).rename('NDBI')

    classified = classify_landcover_from_s2(s2, roi)

    water_mask = classified.eq(3)
    coastal_buffer = water_mask.focal_max(radius=500, units='meters')
    coastal_zone = coastal_buffer.And(water_mask.Not())
    coastal_classified = classified.updateMask(coastal_zone)

    tiles = {
        'rgb': get_tile_url(s2, {'bands': ['B4','B3','B2'], 'min': 0, 'max': 3000}),
        'classified': get_tile_url(classified, {'min': 1, 'max': 4, 'palette': ['228B22','FF4500','4169E1','DAA520']}),
        'ndvi': get_tile_url(ndvi, {'min': -0.1, 'max': 0.8, 'palette': ['red','yellow','green','darkgreen']}),
        'protected': get_tile_url(coastal_classified.eq(1).selfMask(), {'palette': ['00FF00']}),
        'exposed': get_tile_url(coastal_classified.eq(2).selfMask(), {'palette': ['FF0000']}),
        'vulnerable': get_tile_url(coastal_classified.eq(4).selfMask(), {'palette': ['FFA500']}),
    }
    try:
        gmw = gmw_mangrove_mask_2020(roi)
        tiles['gmw_2020'] = get_tile_url(gmw, {'palette': ['00FF88']})
    except Exception:
        tiles['gmw_2020'] = None

    try:
        xm = extra_categorical_masks(s2, classified, roi)
        tiles['bosque_seco'] = get_tile_url(xm['bosque_seco'], {'palette': ['CD853F']})
        tiles['industrial'] = get_tile_url(xm['industrial'], {'palette': ['708090']})
        tiles['contaminacion_agua'] = get_tile_url(xm['contaminacion_agua'], {'palette': ['DA70D6']})
    except Exception:
        tiles['bosque_seco'] = None
        tiles['industrial'] = None
        tiles['contaminacion_agua'] = None
    return tiles

tiles = load_tiles()


# Opacidad “encendida” por capa tesela (misma lógica que antes; tope 0.42 salvo RGB).
def _overlay_tile_opacity_on(flag: str) -> float:
    cap = 0.42
    raw = {
        "classified": 0.92,
        "ndvi": 0.88,
        "gmw": 0.68,
        "prot": 0.95,
        "exp": 0.95,
        "vuln": 0.95,
        "marea": 0.45,
        "inun": 0.78,
        "bosque": 0.78,
        "ind": 0.72,
        "cont": 0.7,
    }.get(flag, cap)
    return min(float(raw), cap)


def _overlay_tile_url(flag: str, tiles_static: dict, sim: dict) -> str | None:
    if flag == "classified":
        return tiles_static.get("classified")
    if flag == "ndvi":
        return tiles_static.get("ndvi")
    if flag == "gmw":
        return tiles_static.get("gmw_2020")
    if flag == "prot":
        return tiles_static.get("protected")
    if flag == "exp":
        return tiles_static.get("exposed")
    if flag == "vuln":
        return tiles_static.get("vulnerable")
    if flag == "marea":
        return sim.get("marea")
    if flag == "inun":
        return sim.get("inundacion")
    if flag == "bosque":
        return tiles_static.get("bosque_seco")
    if flag == "ind":
        return tiles_static.get("industrial")
    if flag == "cont":
        return tiles_static.get("contaminacion_agua")
    return None


class _BoomerangLeafletControls(MacroElement):
    """
    streamlit-folium solo ejecuta JS en el bundle MacroElement; <script> suelto en HTML no corre en el iframe.
    """

    _template = Template(
        """
    {% macro script(this, kwargs) %}
    {{ this.js_body|safe }}
    {% endmacro %}
    """
    )

    def __init__(self, js_body: str):
        super().__init__()
        self._name = "BoomerangLeafletControls"
        self.js_body = js_body


def _scenario_labels_for_js(
    scenario_grid: list[int],
    tide_labels: list[str] | None,
    rain_labels: list[str] | None,
) -> tuple[list[str], list[str]]:
    """Misma longitud que ``scenario_grid``; si faltan etiquetas, se usan porcentajes."""
    n = len(scenario_grid)
    t = tide_labels if tide_labels and len(tide_labels) == n else None
    r = rain_labels if rain_labels and len(rain_labels) == n else None
    t_out = t if t else [f"{g} %" for g in scenario_grid]
    r_out = r if r else [f"{g} %" for g in scenario_grid]
    return t_out, r_out


def _inject_leaflet_map_scripts(
    m: folium.Map,
    panel_specs: list[dict],
    scenario_cache: dict[str, dict],
    scenario_grid: list[int],
    initial_scenario_t: int,
    initial_scenario_r: int,
    legend_groups: list[dict] | None = None,
    scenario_tide_labels: list[str] | None = None,
    scenario_rain_labels: list[str] | None = None,
) -> None:
    """
    Casillas + escenario en JS (opacidad / setUrl). id del mapa en streamlit-folium: map_div.
    localStorage del escenario incluye `py` = clave del run Python para alinear con la barra lateral.
    """
    legends = legend_groups if legend_groups is not None else MAP_LEGEND_GROUPS
    legends_json = json.dumps(legends, ensure_ascii=False)
    cfg_json = json.dumps({"specs": panel_specs}, ensure_ascii=False)
    sc_json = json.dumps(scenario_cache, ensure_ascii=False)
    grid_json = json.dumps(scenario_grid)
    _tl, _rl = _scenario_labels_for_js(
        scenario_grid, scenario_tide_labels, scenario_rain_labels
    )
    grid_tide_labels_json = json.dumps(_tl, ensure_ascii=False)
    grid_rain_labels_json = json.dumps(_rl, ensure_ascii=False)
    it = int(initial_scenario_t)
    ir = int(initial_scenario_r)
    js_body = f"""
(function() {{
  var CONFIG = {cfg_json};
  var LEGENDS = {legends_json};
  var SCENARIO_CACHE = {sc_json};
  var GRID = {grid_json};
  var GRID_TIDE_LABELS = {grid_tide_labels_json};
  var GRID_RAIN_LABELS = {grid_rain_labels_json};
  var MAP_DIV_ID = 'map_div';
  var LS_VIS = 'boomerang_layer_vis';
  var LS_SCN = 'boomerang_scenario_tr';
  var INIT_T = {it};
  var INIT_R = {ir};
  var PYTHON_SCENARIO = INIT_T + '_' + INIT_R;
  var BOOM_BOOT_TRIES = 0;
  var BOOM_BOOT_MAX = 240;

  function getMap() {{
    if (typeof L === 'undefined') return null;
    try {{
      if (typeof map_div !== 'undefined' && map_div && map_div.whenReady && map_div.getContainer)
        return map_div;
    }} catch (e0) {{}}
    try {{
      if (typeof window !== 'undefined' && window.map_div && window.map_div.whenReady)
        return window.map_div;
    }} catch (e1) {{}}
    var el = document.getElementById(MAP_DIV_ID);
    if (!el) {{
      var qs = document.querySelectorAll('.folium-map');
      if (qs && qs.length) el = qs[0];
    }}
    if (!el) return null;
    if (el._leaflet_id != null && L.Map && L.Map._instances && L.Map._instances[el._leaflet_id])
      return L.Map._instances[el._leaflet_id];
    var lc = el.querySelector ? el.querySelector('.leaflet-container') : null;
    if (lc && lc._leaflet_id != null && L.Map && L.Map._instances && L.Map._instances[lc._leaflet_id])
      return L.Map._instances[lc._leaflet_id];
    if (L.Map && L.Map._instances) {{
      var inst = L.Map._instances;
      for (var k in inst) {{
        if (!Object.prototype.hasOwnProperty.call(inst, k)) continue;
        var mm = inst[k];
        if (!mm || !mm.getContainer) continue;
        var c = mm.getContainer();
        if (c === el || (el.contains && el.contains(c))) return mm;
      }}
    }}
    return null;
  }}

  function boot() {{
    BOOM_BOOT_TRIES += 1;
    var map = getMap();
    if (!map) {{
      if (BOOM_BOOT_TRIES < BOOM_BOOT_MAX) setTimeout(boot, 50);
      return;
    }}
    map.whenReady(function() {{
      var zfix = document.createElement('style');
      zfix.textContent = '#map_div .leaflet-control-container,' +
        '#map_div .leaflet-top.leaflet-left,' +
        '#map_div .leaflet-top.leaflet-right,' +
        '#map_div .leaflet-bottom.leaflet-right {{ z-index: 10002 !important; }}' +
        '#map_div .boomerang-layer-panel input[type=checkbox] {{ width:18px;height:18px;flex-shrink:0;margin-top:2px; }}';
      document.head.appendChild(zfix);
      var byId = {{}};
      map.eachLayer(function(layer) {{
        var o = layer.options || {{}};
        var bid = o.boomId || o.boom_id;
        if (bid) byId[bid] = layer;
      }});

      var stored = {{}};
      try {{ stored = JSON.parse(localStorage.getItem(LS_VIS) || '{{}}'); }} catch (e) {{}}
      var vis = {{}};
      var boomRefreshLegends = function() {{}};
      var boomLegendSetOpen = function() {{}};
      function anyLayerOn() {{
        var on = false;
        CONFIG.specs.forEach(function(ss) {{ if (vis[ss.boom_id]) on = true; }});
        return on;
      }}
      function applyOne(id, on) {{
        var spec = CONFIG.specs.find(function(s) {{ return s.boom_id === id; }});
        var layer = byId[id];
        if (!spec || !layer) return;
        if (spec.kind === 'tile') {{
          layer.setOpacity(on ? spec.opacity_on : 0);
        }} else if (spec.kind === 'geojson') {{
          if (on) {{ if (!map.hasLayer(layer)) map.addLayer(layer); }}
          else {{ if (map.hasLayer(layer)) map.removeLayer(layer); }}
        }}
      }}
      CONFIG.specs.forEach(function(s) {{
        vis[s.boom_id] = Object.prototype.hasOwnProperty.call(stored, s.boom_id)
          ? !!stored[s.boom_id] : !!s.initial;
        applyOne(s.boom_id, vis[s.boom_id]);
      }});
      function saveVis() {{
        try {{ localStorage.setItem(LS_VIS, JSON.stringify(vis)); }} catch (e) {{}}
      }}

      var panel = L.control({{position: 'topleft'}});
      panel.onAdd = function() {{
        var div = L.DomUtil.create('div', 'boomerang-layer-panel');
        div.style.cssText = 'background:rgba(255,255,255,0.96);padding:12px 14px;border-radius:10px;max-width:320px;max-height:min(85vh,640px);overflow:auto;font:14px/1.4 system-ui,Segoe UI,sans-serif;box-shadow:0 2px 12px rgba(0,0,0,0.25);color:#111;z-index:1000001;position:relative;';
        L.DomEvent.disableClickPropagation(div);
        L.DomEvent.disableScrollPropagation(div);
        var h = document.createElement('div');
        h.textContent = 'What to show on the map';
        h.style.cssText = 'font-weight:600;margin-bottom:6px;font-size:16px;';
        div.appendChild(h);
        var hint = document.createElement('div');
        hint.textContent = 'Turn layers on or off. Satellite imagery stays underneath; the page does not reload.';
        hint.style.cssText = 'font-size:12px;color:#444;margin-bottom:8px;line-height:1.4;';
        div.appendChild(hint);
        var checkboxes = [];
        CONFIG.specs.forEach(function(s) {{
          var row = document.createElement('label');
          row.style.cssText = 'display:flex;align-items:flex-start;gap:10px;margin:6px 0;cursor:pointer;';
          if (s.help) row.title = s.help;
          var cb = document.createElement('input');
          cb.type = 'checkbox';
          cb.setAttribute('data-boom', s.boom_id);
          if (s.help) cb.title = s.help;
          cb.checked = !!vis[s.boom_id];
          cb.addEventListener('change', function() {{
            vis[s.boom_id] = cb.checked;
            applyOne(s.boom_id, vis[s.boom_id]);
            saveVis();
            boomRefreshLegends();
            if (cb.checked) {{
              boomLegendSetOpen(true);
            }} else {{
              if (!anyLayerOn()) boomLegendSetOpen(false);
            }}
          }});
          checkboxes.push(cb);
          var span = document.createElement('span');
          span.textContent = s.label;
          if (s.help) span.title = s.help;
          span.style.cssText = 'line-height:1.4;font-size:14px;';
          row.appendChild(cb);
          row.appendChild(span);
          div.appendChild(row);
        }});
        var rowBtn = document.createElement('div');
        rowBtn.style.cssText = 'display:flex;gap:8px;margin-top:10px;padding-top:8px;border-top:1px solid #ddd;';
        function syncChecks() {{
          checkboxes.forEach(function(cb) {{
            var id = cb.getAttribute('data-boom');
            cb.checked = !!vis[id];
          }});
        }}
        function setAll(on) {{
          CONFIG.specs.forEach(function(s) {{
            vis[s.boom_id] = on;
            applyOne(s.boom_id, on);
          }});
          saveVis();
          syncChecks();
          boomRefreshLegends();
          if (on) boomLegendSetOpen(true);
          else boomLegendSetOpen(false);
        }}
        var b1 = document.createElement('button');
        b1.type = 'button';
        b1.textContent = 'Show all';
        b1.title = 'Turn on every layer in the list.';
        b1.style.cssText = 'flex:1;padding:7px 10px;font-size:13px;border-radius:6px;border:1px solid #ccc;background:#f4f4f4;cursor:pointer;';
        b1.onclick = function() {{ setAll(true); }};
        var b2 = document.createElement('button');
        b2.type = 'button';
        b2.textContent = 'Hide all';
        b2.title = 'Turn off every layer in the list.';
        b2.style.cssText = b1.style.cssText;
        b2.onclick = function() {{ setAll(false); }};
        rowBtn.appendChild(b1);
        rowBtn.appendChild(b2);
        div.appendChild(rowBtn);
        var legNote = document.createElement('div');
        legNote.textContent = 'Legends (bottom right) open when you turn a layer on; use the button to close.';
        legNote.style.cssText = 'font-size:11px;color:#666;margin-top:10px;padding-top:8px;border-top:1px solid #eee;line-height:1.4;';
        div.appendChild(legNote);
        return div;
      }};
      panel.addTo(map);

      var legendToggle = L.control({{position: 'bottomright'}});
      legendToggle.onAdd = function() {{
        var wrap = L.DomUtil.create('div', 'boomerang-legend-toggle');
        wrap.style.cssText = 'display:flex;flex-direction:column;align-items:flex-end;gap:8px;margin:0 8px 10px 0;';
        L.DomEvent.disableClickPropagation(wrap);
        L.DomEvent.disableScrollPropagation(wrap);
        var panelEl = document.createElement('div');
        panelEl.style.cssText = 'display:none;background:rgba(255,255,255,0.97);padding:12px 14px;border-radius:10px;max-width:320px;max-height:min(55vh,460px);overflow-y:auto;font:14px/1.4 system-ui,Segoe UI,sans-serif;box-shadow:0 2px 14px rgba(0,0,0,0.28);color:#111;text-align:left;';
        var legH = document.createElement('div');
        legH.textContent = 'Leyendas (solo capas activas)';
        legH.style.cssText = 'font-weight:600;font-size:15px;margin-bottom:6px;color:#222;';
        panelEl.appendChild(legH);
        var legHint = document.createElement('div');
        legHint.textContent = 'Se muestran solo las leyendas de las casillas marcadas a la izquierda.';
        legHint.style.cssText = 'font-size:12px;color:#666;margin-bottom:8px;line-height:1.4;';
        panelEl.appendChild(legHint);
        var legendBody = document.createElement('div');
        legendBody.className = 'boomerang-legend-body';
        panelEl.appendChild(legendBody);
        function fillLegendBody() {{
          legendBody.innerHTML = '';
          var any = false;
          LEGENDS.forEach(function(g) {{
            var ids = g.layer_ids || [];
            if (!ids.length) return;
            var show = ids.some(function(id) {{ return !!vis[id]; }});
            if (!show) return;
            any = true;
            var gt = document.createElement('div');
            gt.style.cssText = 'font-size:13px;font-weight:600;color:#333;margin:8px 0 4px;';
            gt.textContent = g.title;
            legendBody.appendChild(gt);
            g.rows.forEach(function(r) {{
              var lr = document.createElement('div');
              lr.style.cssText = 'display:flex;align-items:flex-start;gap:8px;margin:4px 0;font-size:12px;line-height:1.4;';
              var sw = document.createElement('span');
              sw.style.cssText = 'width:14px;height:14px;border-radius:3px;border:1px solid #bbb;flex-shrink:0;margin-top:3px;background:' + (r.hex || '#ccc');
              var tx = document.createElement('span');
              tx.textContent = r.text;
              tx.style.cssText = 'color:#444;';
              lr.appendChild(sw);
              lr.appendChild(tx);
              legendBody.appendChild(lr);
            }});
          }});
          if (!any) {{
            var empty = document.createElement('div');
            empty.textContent = 'No layer is on. Check at least one layer on the left to see colours here.';
            empty.style.cssText = 'font-size:12px;color:#666;line-height:1.4;';
            legendBody.appendChild(empty);
          }}
        }}
        boomRefreshLegends = fillLegendBody;
        fillLegendBody();
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.textContent = 'Legends';
        btn.title = 'Open or close the colour guide (for active layers)';
        btn.style.cssText = 'padding:10px 18px;font-size:14px;font-weight:600;border-radius:8px;border:1px solid #bbb;background:linear-gradient(180deg,#fff,#f0f0f0);box-shadow:0 2px 10px rgba(0,0,0,0.22);cursor:pointer;color:#222;';
        var open = false;
        function syncLegendPanelUi() {{
          panelEl.style.display = open ? 'block' : 'none';
          btn.textContent = open ? 'Close legends' : 'Legends';
          btn.title = open ? 'Hide the legend panel' : 'See colours for active layers';
        }}
        boomLegendSetOpen = function(wantOpen) {{
          open = !!wantOpen;
          if (open) fillLegendBody();
          syncLegendPanelUi();
        }};
        btn.onclick = function() {{
          open = !open;
          if (open) fillLegendBody();
          syncLegendPanelUi();
        }};
        if (anyLayerOn()) {{
          open = true;
          fillLegendBody();
          syncLegendPanelUi();
        }}
        wrap.appendChild(panelEl);
        wrap.appendChild(btn);
        return wrap;
      }};
      legendToggle.addTo(map);

      if (Object.keys(SCENARIO_CACHE).length === 0) return;

      var scT = INIT_T, scR = INIT_R;
      try {{
        var sc = JSON.parse(localStorage.getItem(LS_SCN) || 'null');
        if (sc && sc.py === PYTHON_SCENARIO) {{
          if (sc.t !== undefined && GRID.indexOf(Number(sc.t)) >= 0) scT = Number(sc.t);
          if (sc.r !== undefined && GRID.indexOf(Number(sc.r)) >= 0) scR = Number(sc.r);
        }}
      }} catch (e2) {{}}

      function applyScenario(kt, kr) {{
        var key = kt + '_' + kr;
        var pack = SCENARIO_CACHE[key];
        if (!pack) return;
        var lr = byId['rgb'], lm = byId['marea'], li = byId['inun'];
        if (lr && pack.rgb && typeof lr.setUrl === 'function') lr.setUrl(pack.rgb);
        if (lm && pack.marea && typeof lm.setUrl === 'function') lm.setUrl(pack.marea);
        if (li && pack.inundacion && typeof li.setUrl === 'function') li.setUrl(pack.inundacion);
        try {{
          localStorage.setItem(LS_SCN, JSON.stringify({{t: kt, r: kr, py: PYTHON_SCENARIO}}));
        }} catch (e3) {{}}
      }}

      applyScenario(scT, scR);
    }});
  }}
  if (document.readyState === 'loading')
    document.addEventListener('DOMContentLoaded', boot);
  else
    boot();
}})();
"""
    _BoomerangLeafletControls(js_body).add_to(m)


def make_map(
    sim: dict,
    tiles_static: dict,
    geojson_fc=None,
    fit_bounds=None,
    lock_roi: bool = True,
    apply_fit_bounds: bool = True,
    scenario_cache: dict[str, dict] | None = None,
    scenario_grid: list[int] | None = None,
    initial_scenario_t: int = 0,
    initial_scenario_r: int = 0,
    scenario_tide_labels: list[str] | None = None,
    scenario_rain_labels: list[str] | None = None,
    show_demo_zones_on_map: bool = False,
):
    """
    Todas las teselas GEE con boom_id; visibilidad y escenario en JS (opacidad / setUrl).
    ``show_demo_zones_on_map``: polígonos de barrios (GeoJSON); por defecto ocultos en satélite.
    """
    map_kw: dict = {
        'location': ROI_CENTER,
        'zoom_start': 12,
        'tiles': DEFAULT_BASEMAP_TILES,
        'attr': DEFAULT_BASEMAP_ATTR,
        'control_scale': True,
    }
    if lock_roi:
        map_kw['max_bounds'] = True
        map_kw['min_lat'] = ROI_FIT_BOUNDS[0][0]
        map_kw['max_lat'] = ROI_FIT_BOUNDS[1][0]
        map_kw['min_lon'] = ROI_FIT_BOUNDS[0][1]
        map_kw['max_lon'] = ROI_FIT_BOUNDS[1][1]
    m = folium.Map(**map_kw)

    panel_specs: list[dict] = []

    # Sentinel-2 RGB (siempre presente; no entra en el panel de casillas)
    folium.TileLayer(
        tiles=sim["rgb"],
        attr='Google Earth Engine',
        name=LAYER_FLAG_LABELS["rgb"],
        overlay=True,
        opacity=0.48,
        control=False,
        boom_id='rgb',
    ).add_to(m)

    for flag in OVERLAY_FLAG_KEYS:
        if flag == "zones":
            continue
        url = _overlay_tile_url(flag, tiles_static, sim)
        if not url:
            continue
        op_on = _overlay_tile_opacity_on(flag)
        folium.TileLayer(
            tiles=url,
            attr='Google Earth Engine',
            name=LAYER_FLAG_LABELS.get(flag, flag),
            overlay=True,
            opacity=op_on,
            control=False,
            boom_id=flag,
        ).add_to(m)
        ui = LAYER_PANEL_UI.get(flag, {})
        panel_specs.append(
            {
                "boom_id": flag,
                "label": ui.get("label", LAYER_FLAG_LABELS.get(flag, flag)),
                "help": ui.get("help", ""),
                "kind": "tile",
                "opacity_on": op_on,
                "initial": True,
            }
        )

    if show_demo_zones_on_map and geojson_fc and geojson_fc.get("features"):
        # Sin GeoJsonTooltip: el JS extra + iframe de streamlit-folium a veces rompe el visor.
        # El nombre del barrio está en el selector debajo del mapa.
        gj = folium.GeoJson(
            geojson_fc,
            name="Neighbourhood reference (demo)",
            style_function=lambda _feat: {
                "fillColor": "#3388ff",
                "color": "#1a5fb4",
                "weight": 1.2,
                "fillOpacity": 0.06,
            },
            boom_id="zones",
            control=False,
        )
        gj.add_to(m)
        zui = LAYER_PANEL_UI.get("zones", {})
        panel_specs.append(
            {
                "boom_id": "zones",
                "label": zui.get("label", "Neighbourhoods (demo)"),
                "help": zui.get("help", ""),
                "kind": "geojson",
                "opacity_on": 1.0,
                "initial": True,
            }
        )

    if lock_roi:
        folium.Rectangle(
            bounds=ROI_FIT_BOUNDS,
            color='#e8e8e8',
            weight=2,
            fill=False,
        ).add_to(m)

    fb = fit_bounds if fit_bounds is not None else (ROI_FIT_BOUNDS if lock_roi else None)
    if apply_fit_bounds and fb is not None:
        m.fit_bounds(fb, padding=(2, 2), max_zoom=18)

    _inject_leaflet_map_scripts(
        m,
        panel_specs,
        scenario_cache=scenario_cache or {},
        scenario_grid=scenario_grid or SCENARIO_GRID_VALUES,
        initial_scenario_t=initial_scenario_t,
        initial_scenario_r=initial_scenario_r,
        scenario_tide_labels=scenario_tide_labels,
        scenario_rain_labels=scenario_rain_labels,
    )
    return m


@st.cache_data(ttl=3600, show_spinner="Running simulation in Earth Engine…")
def tide_simulation_tiles(tide_pct: float, rain_stress_pct: float):
    """
    Proxy visual alineado con `inundacion_mask` en gee_layers: marea + estrés de lluvia.
    tide_pct y rain_stress_pct 0–100 amplían el buffer desde la máscara de agua.
    """
    s2, classified = _s2_classified_median(roi)
    flood = inundacion_mask(classified, tide_pct, rain_stress_pct)
    water = classified.eq(3)
    buffer_m = inundacion_buffer_meters(tide_pct, rain_stress_pct)
    expanded = water.focal_max(radius=buffer_m, units='meters')

    return {
        'rgb': get_tile_url(s2, {'bands': ['B4', 'B3', 'B2'], 'min': 0, 'max': 3000}),
        'marea': get_tile_url(expanded.selfMask(), {'palette': ['1E90FF']}),
        'inundacion': get_tile_url(flood, {'palette': ['DC143C']}),
    }


@st.cache_data(ttl=3600, show_spinner="Precomputing scenario grid (GEE)…")
def precache_scenario_tiles_grid(grid_tuple: tuple[int, ...]) -> dict[str, dict]:
    """Todas las combinaciones (marea × lluvia) en la rejilla; cache para el mapa y métricas."""
    out: dict[str, dict] = {}
    for t in grid_tuple:
        for r in grid_tuple:
            out[f"{t}_{r}"] = tide_simulation_tiles(float(t), float(r))
    return out


def _snap_to_grid(v: float, grid: list[int]) -> int:
    return min(grid, key=lambda g: abs(float(g) - float(v)))


@st.cache_data(ttl=3600, show_spinner="Loading neighbourhoods (Earth Engine)…")
def zones_geojson_cached():
    return zones_geojson_for_map(roi)


@st.cache_data(ttl=3600, show_spinner="Computing zone ranking (GEE)…")
def zone_ranking_cached(tide_pct: float, rain_stress_pct: float):
    return zone_inundacion_ranking(roi, tide_pct, rain_stress_pct)


@st.cache_data(ttl=1800, show_spinner="Syncing forecast, sea level, and alert engine…")
def alert_bundle_cached():
    daily, err = fetch_open_meteo_precip_forecast()
    marine, merr = fetch_marine_sea_level_hourly()
    alerts, metrics = build_alerts(daily, err, marine, merr)
    return alerts, metrics


@st.cache_data(ttl=3600, show_spinner="Computing flood proxy % (GEE)…")
def flood_proxy_stats_cached(tide_pct: float, rain_stress_pct: float):
    return compute_flood_proxy_stats(roi, tide_pct=tide_pct, rain_stress_pct=rain_stress_pct)


@st.cache_data(ttl=1800, show_spinner="Loading historical rain (Archive)…")
def historical_rain_bundle():
    arch, aerr = fetch_open_meteo_archive_precipitation(90)
    daily, _ = fetch_open_meteo_precip_forecast()
    ctx = forecast_vs_history_context(arch, daily)
    return arch, aerr, ctx


def _daily_precip_chart_df(chart_rows: list) -> tuple[pd.DataFrame, list]:
    """Etiquetas cortas sin año (ej. «27 mar») y orden cronológico para el eje X horizontal."""
    meses = (
        "ene", "feb", "mar", "abr", "may", "jun",
        "jul", "ago", "sep", "oct", "nov", "dic",
    )
    rows = []
    for r in chart_rows:
        iso = r["Fecha"]
        d = datetime.strptime(iso, "%Y-%m-%d")
        label = f"{d.day} {meses[d.month - 1]}"
        rows.append({"Dia": label, "mm": r["mm"], "_sort": iso})
    df = pd.DataFrame(rows).sort_values("_sort")
    order = df["Dia"].tolist()
    return df[["Dia", "mm"]], order


def _render_map_integrated(
    tide_pct: float,
    rain_stress_pct: float,
    tide_label: str,
    rain_label: str,
) -> None:
    """Folium + paneles Leaflet (opacidad / setUrl — sin rerun al cambiar capas o escenario en el iframe)."""
    st.markdown("##### Mapa")
    st.caption(
        "A la izquierda del mapa eliges qué ver; abajo a la derecha, las leyendas. Arriba a la derecha, la prueba de marea y lluvia."
    )
    grid = SCENARIO_GRID_VALUES
    scenario_cache = precache_scenario_tiles_grid(tuple(grid))
    it = _snap_to_grid(tide_pct, grid)
    ir = _snap_to_grid(rain_stress_pct, grid)
    sim = scenario_cache[f"{it}_{ir}"]
    show_demo_zones_on_map = False
    geo_fc = {"type": "FeatureCollection", "features": []}
    if show_demo_zones_on_map:
        try:
            geo_fc = zones_geojson_cached()
        except Exception as ex:
            st.warning(
                f"No se pudieron cargar los recuadros de barrios (Earth Engine). Detalle: {ex}"
            )
            geo_fc = {"type": "FeatureCollection", "features": []}
    m_main = make_map(
        sim,
        tiles,
        geojson_fc=geo_fc,
        fit_bounds=None,
        apply_fit_bounds=False,
        scenario_cache=scenario_cache,
        scenario_grid=grid,
        initial_scenario_t=it,
        initial_scenario_r=ir,
        scenario_tide_labels=TIDE_SCENARIO_LABELS,
        scenario_rain_labels=RAIN_SCENARIO_LABELS,
        show_demo_zones_on_map=show_demo_zones_on_map,
    )
    _map_center, _map_zoom = _zoom_center_for_roi_panel(
        MAIN_MAP_WIDTH_REF_PX, float(MAIN_MAP_HEIGHT_PX)
    )
    st_folium(
        m_main,
        height=MAIN_MAP_HEIGHT_PX,
        use_container_width=True,
        key="folium_main",
        zoom=_map_zoom,
        center=_map_center,
    )
    st.markdown("##### ¿Hasta dónde podría llegar el agua?")
    st.caption(
        "Elige barrio y día. A la izquierda, una **prueba** con el mapa; a la derecha, un **día concreto** con lluvia y marea."
    )
    _zlist = demo_zone_names()
    _zi = _zlist.index("Cristo del Consuelo") if "Cristo del Consuelo" in _zlist else 0
    _zone_pick = st.selectbox(
        "Barrio (referencia en mapa)",
        _zlist,
        index=_zi,
        key="demo_zone_alert_pick",
    )
    _alerts, _am = alert_bundle_cached()
    _rows = _am.get("daily_precip_rows") or []
    _f_iso: str | None = None
    _f_mm: float | None = None
    if _rows:
        _peak_iso, _ = peak_precipitation_day_72h(_rows)
        _def_idx = max(
            range(len(_rows)),
            key=lambda i: float(_rows[i].get("mm", 0) or 0),
        )
        st.caption(
            f"Día con más lluvia a la vista: **{format_day_es(_peak_iso)}**. Puedes elegir otro abajo."
        )
        _idx_day = st.selectbox(
            "Día",
            list(range(len(_rows))),
            index=_def_idx,
            format_func=lambda i: (
                f"{format_day_es(_rows[i]['Fecha'])} — {float(_rows[i].get('mm', 0) or 0):.1f} mm"
            ),
            key="demo_forecast_day_pick",
        )
        _rp = _rows[_idx_day]
        _f_iso = str(_rp.get("Fecha") or "")
        _f_mm = float(_rp.get("mm", 0) or 0)
    else:
        st.info(
            "No hay filas de pronóstico de lluvia todavía. Usa **«Actualizar pronóstico y alertas»** en la barra lateral."
        )
    _sim_h, _fc_h = citizen_flood_cards_pair(
        _zone_pick,
        tide_pct,
        rain_stress_pct,
        _f_iso,
        _f_mm,
        _am.get("marine_hourly"),
        tide_label=tide_label,
        rain_label=rain_label,
    )
    if _fc_h:
        _col_sim, _col_fc = st.columns(2, gap="medium")
        with _col_sim:
            st.markdown(_sim_h, unsafe_allow_html=True)
        with _col_fc:
            st.markdown(_fc_h, unsafe_allow_html=True)
    else:
        st.markdown(_sim_h, unsafe_allow_html=True)


# --- HEADER ---
st.title("Boomerang Dashboard")
st.markdown("**Mangroves as flood barriers in Greater Guayaquil**")
st.markdown("Sentinel-2 + Landsat 8 | Google Earth Engine | SpaceHACK 2026")
st.sidebar.markdown("### Boomerang Alert Engine")
st.sidebar.caption(
    "Combines GEE + rain + sea level (Open-Meteo Forecast + Marine). "
    "Does not replace INAMHI / ECU911 / INOCAR."
)
st.sidebar.link_button(
    "Open-Meteo (rain forecast)",
    "https://open-meteo.com/",
    help="Public API; attribution in reports.",
)
st.sidebar.link_button(
    "Open-Meteo Marine (tide / sea level)",
    "https://open-meteo.com/en/docs/marine-weather-api",
    help="sea_level_height_msl — tide proxy in the Gulf.",
)
st.sidebar.link_button(
    "Global Mangrove Watch (paper + data)",
    "https://doi.org/10.5281/zenodo.6894273",
    help="GMW v3/v4 — cite in slides; 2020 layer in GEE via sat-io.",
)
if st.sidebar.button("Refresh forecast & alerts", use_container_width=True):
    alert_bundle_cached.clear()
    st.rerun()

st.sidebar.markdown("### Scenario (tide + rain)")
_grid_n = len(SCENARIO_GRID_VALUES)
_tide_i = st.sidebar.selectbox(
    "Tide (simulation from water mask)",
    range(_grid_n),
    index=2,
    format_func=lambda i: TIDE_SCENARIO_LABELS[i],
    key="tide_scenario_idx",
    help=(
        "Qualitative estuary-style levels (low → high tide). "
        "Controls morphological buffer from water mask (~30–680 m); does not replace INOCAR tide tables."
    ),
)
_rain_i = st.sidebar.selectbox(
    "Rain (stress / runoff in simulation)",
    range(_grid_n),
    index=1,
    format_func=lambda i: RAIN_SCENARIO_LABELS[i],
    key="rain_scenario_idx",
    help=(
        "Indicative daily accumulation bands for tropical coastal zone; "
        "in the model they only increase the flood proxy buffer (~150 m max per rain). Not an official forecast."
    ),
)
tide_pct = float(SCENARIO_GRID_VALUES[_tide_i])
rain_stress_pct = float(SCENARIO_GRID_VALUES[_rain_i])
buffer_m = inundacion_buffer_meters(tide_pct, rain_stress_pct)
st.sidebar.caption(f"Combined buffer ~{buffer_m} m from water (morphological proxy; no DEM).")
st.sidebar.markdown("##### Integrated map")
st.sidebar.caption(
    "**Layers** are toggled in the map viewer (no reload). "
    "**Scenario** (tide and rain) only here on the left; aligns metrics and tables below."
)
st.sidebar.caption("Below: tables and charts (no duplicate maps).")

# --- TOP METRICS ---
col1, col2, col3, col4 = st.columns(4)
col1.metric("Protected Coast", "48.7%")
col2.metric("Exposed Coast", "51.3%", delta="+9,367 ha at risk", delta_color="inverse")
col3.metric("Urban Expansion", "+11,247 ha", delta="2013–2024", delta_color="inverse")
col4.metric("Mangrove Lost", "−17,202 ha", delta="since 2013", delta_color="inverse")

st.divider()

st.subheader("Integrated Map — Greater Guayaquil")
st.markdown(
    "Single map with **satellite** imagery. Toggle layers in the map panel; "
    "the simulation scenario is set in the sidebar (same figures as below)."
)

_render_map_integrated(
    tide_pct,
    rain_stress_pct,
    TIDE_SCENARIO_LABELS[_tide_i],
    RAIN_SCENARIO_LABELS[_rain_i],
)
st.caption(
    "Abajo, las cifras siguen el escenario elegido a la izquierda. Los recuadros del mapa son orientativos, no límites oficiales."
)
with st.expander("Technical details (layers & sources)", expanded=False):
    st.caption(
        "Classification: mangrove (GMW 2020 + wet coastal spectral signature), dry forest, urban, water, soil. "
        "GMW: Bunting et al., 2022 · CC BY 4.0."
    )

fp = flood_proxy_stats_cached(float(tide_pct), float(rain_stress_pct))
rank_rows = zone_ranking_cached(float(tide_pct), float(rain_stress_pct))
fp_c1, fp_c2, fp_c3 = st.columns(3)
fp_c1.metric("Flood proxy area (ROI)", f"{fp.get('ha_inundacion_proxy', 0)} ha")
fp_c2.metric("% ROI under flood proxy", f"{fp.get('pct_roi_inundacion_proxy', 0)} %")
fp_c3.metric("Combined buffer approx.", f"{buffer_m} m")

st.markdown("##### Zones with largest flooded area (proxy) — ranked")
st.dataframe(pd.DataFrame(rank_rows), hide_index=True, use_container_width=True)

st.markdown("##### Recent historical rain: wettest days and height reference")
st.caption(
    "Rain data: **Open-Meteo Archive** (daily reanalysis for Guayaquil). "
    "The height column uses the **same rule** as the 'Today's forecast' card (heuristic mm → cm), not a measured hydrograph."
)
_arch_rows, _arch_err, _ = historical_rain_bundle()
if _arch_err:
    st.warning(f"Could not load rain history: {_arch_err}")
elif _arch_rows is not None and len(_arch_rows) > 0:
    _znames = demo_zone_names()
    _zi_hist = (
        _znames.index("Urdesa / Kennedy")
        if "Urdesa / Kennedy" in _znames
        else 0
    )
    _zone_hist = st.selectbox(
        "Neighborhood for height reference (~170 cm figure)",
        _znames,
        index=_zi_hist,
        key="hist_rainiest_zone_ref",
        help="Same per-neighborhood factor as in the flood demo; not observed water elevation.",
    )
    _top_n = 15
    _sorted_days = sorted(
        _arch_rows,
        key=lambda r: float(r.get("mm", 0) or 0),
        reverse=True,
    )[:_top_n]
    _hist_table = []
    for _r in _sorted_days:
        _mm = float(_r.get("mm", 0) or 0)
        _fe = str(_r.get("Fecha", ""))
        _dcm = water_depth_cm_from_forecast_mm(_zone_hist, _mm)
        _hist_table.append(
            {
                "Día": format_day_es(_fe),
                "mm (Archive)": round(_mm, 1),
                "Ref. altura (cm)": int(round(_dcm)),
                "Lenguaje coloquial": human_depth_phrase_cm(_dcm),
            }
        )
    st.dataframe(pd.DataFrame(_hist_table), hide_index=True, use_container_width=True)
    st.info(
        "**This is not 'how deep the water got' on the street.** That would require gauges, field photos, or event flood maps "
        "(e.g. satellite radar). Here you only see **modelled rain** + a **fixed rule** for the human figure. "
        "To compare with reality, use **ECU911** reports, local news, or post-event studies when available."
    )
    with st.expander("Where does the water level in this dashboard come from?", expanded=False):
        st.markdown(
            """
- **'Today's forecast' card (cm):** converts **mm/day** from the forecast into centimetres using a fixed formula
  and a **per-neighbourhood factor** (simulated relief / proximity to estero). **Not** from a hydraulic model or DEM.
- **Tide in that card:** daily amplitude from the **Marine API** (`sea_level_height_msl` range) transformed with fixed coefficients.
- **Red map (flood proxy):** in GEE, **buffer** from the water mask + sidebar scenario;
  urban/soil nearby without mangrove = coloured area. **Does not** simulate actual water surface elevation.
- **Consistency:** algorithms are **intentionally aligned** (same demo philosophy), not calibrated with field measurements.
            """
        )
elif _arch_rows is not None and len(_arch_rows) == 0:
    st.info(
        "El Archive respondió sin días en el rango pedido. Prueba **Actualizar pronóstico** o revisa la red / fechas en el código."
    )
else:
    st.caption("Sin datos de histórico (respuesta vacía).")

with st.expander("Land cover classification & tables (2024)", expanded=False):
    st.subheader("Land Cover Classification — 2024")
    st.markdown(
        "**Green (S2)** = Mangrove (NDVI/MNDWI thresholds) | **Red** = Urban | **Blue** = Water | **Gold** = Soil. "
        "In the **map layer panel** activate **S2 Classification** and **GMW** if needed."
    )
    st.caption(
        "GMW: Bunting et al., 2022 · annual layer 2020 `projects/sat-io/.../GMW_MNG_2020` · CC BY 4.0 · "
        "Differences vs S2: dates, SAR/optical methodology and resolution."
    )

    col_a, col_b = st.columns(2)
    with col_a:
        df_areas = pd.DataFrame({
            'Class': ['Mangrove/Vegetation', 'Urban Zone', 'Water', 'Soil/Agriculture'],
            'Hectares': [48851, 26502, 4622, 116834],
            'Percentage': ['24.8%', '13.5%', '2.3%', '59.4%']
        })
        st.dataframe(df_areas, hide_index=True, use_container_width=True)
    with col_b:
        st.metric("Avg Mangrove NDVI", "0.67")
        st.warning("MODERATE status — mangrove needs attention")

with st.expander("Coastal flood protection", expanded=False):
    st.subheader("Coastal Flood Protection")
    st.markdown("500m strip from water: **green** = protected, **red** = exposed urban, **orange** = vulnerable")
    st.info(
        "In the **map layer panel**, activate **Protected / Exposed / Vulnerable** and **GMW**."
    )

    col_c, col_d = st.columns(2)
    with col_c:
        st.metric("Protected Coast", "8,888 ha (48.7%)")
        st.success("Areas with natural mangrove buffer")
    with col_d:
        st.metric("Urban Directly Facing Water", "5,066 ha")
        st.error("No mangrove buffer against flooding")

with st.expander("Boomerang Alert Center (prototype)", expanded=False):
    st.subheader("Boomerang Alert Center (prototype)")
    st.markdown(
        "Rule engine: **GEE** (coast/mangrove) + **rain** (Forecast API) + **sea level** "
        "(Marine API, Gulf cell) + **index 0–100** + **economic proxy** — MCC track."
    )

    alerts, am = alert_bundle_cached()

    ri = am.get("risk_index_0_100")
    if ri is not None:
        if ri >= 72:
            st.error(
                f"**High compound risk** — index **{ri}/100** "
                "(rain + modelled sea level + exposed coast)."
            )
        elif ri >= 45:
            st.warning(f"**Moderate compound risk** — Boomerang index **{ri}/100**.")
        else:
            st.success(f"**Low compound risk** — index **{ri}/100** (keep monitoring).")
        st.progress(min(100, max(0, ri)) / 100.0, text=f"Boomerang Index (0–100): {ri}")

    k1, k2, k3, k4 = st.columns(4)
    k1.metric(
        "72h forecast (max mm/d)",
        am.get("max_precip_72h_mm") if am.get("max_precip_72h_mm") is not None else "—",
    )
    k2.metric(
        "Max rain prob. 72h (%)",
        int(am["max_precip_prob_72h"]) if am.get("max_precip_prob_72h") is not None else "—",
    )
    k3.metric(
        "Rain total 7d (mm)",
        am.get("precip_sum_7d_mm") if am.get("precip_sum_7d_mm") is not None else "—",
    )
    k4.metric(
        "Economic exposure (proxy MUSD/y)",
        f"{am['economic_exposure_proxy_million_usd']:.1f}" if am.get("economic_exposure_proxy_million_usd") is not None else "—",
    )

    s1, s2, s3 = st.columns(3)
    s1.metric(
        "Sea level max 72h (m)",
        am.get("sea_level_max_72h_m") if am.get("sea_level_max_72h_m") is not None else "—",
        help="Open-Meteo Marine, Gulf cell; includes modelled tide (~8 km).",
    )
    s2.metric(
        "Tidal range 72h (m)",
        am.get("sea_level_range_72h_m") if am.get("sea_level_range_72h_m") is not None else "—",
    )
    hw = am.get("marine_high_water_flag")
    s3.metric(
        "Relatively high water (72h)",
        "Yes" if hw is True else ("No" if hw is False else "—"),
        help="Max in 72h ≥ 80th percentile of the week (tide peak proxy).",
    )
    st.caption(am.get("marine_point_label", ""))

    chart_rows = am.get("daily_precip_rows") or []
    if chart_rows:
        st.markdown("##### Daily rain forecast (next days)")
        df_chart, dia_order = _daily_precip_chart_df(chart_rows)
        precip_chart = (
            alt.Chart(df_chart)
            .mark_bar(color="#4a9eff", cornerRadiusEnd=2)
            .encode(
                x=alt.X(
                    "Dia:N",
                    sort=dia_order,
                    axis=alt.Axis(labelAngle=0, labelPadding=10, title=None),
                ),
                y=alt.Y("mm:Q", title="mm"),
                tooltip=[
                    alt.Tooltip("Dia", title="Day"),
                    alt.Tooltip("mm:Q", title="mm", format=".1f"),
                ],
            )
            .properties(height=260)
        )
        st.altair_chart(precip_chart, use_container_width=True)

    st.caption(
        f"Last sync UTC: **{am.get('updated_utc', '—')}** · "
        "Rain: Forecast API · Sea level: Marine API · Index: demo heuristic (not official)."
    )

    st.markdown("#### Alert queue (sorted by severity)")
    rows = alerts_to_dataframe_rows(alerts)
    df_alerts = pd.DataFrame(rows)[["Severity", "Title", "Action"]]
    st.dataframe(df_alerts, hide_index=True, use_container_width=True, height=min(320, 60 + len(alerts) * 38))

    with st.expander("Full alert text (for demo)"):
        for i, a in enumerate(alerts, 1):
            st.markdown(f"**{i}. {a.title}** (`{SEVERITY_LABEL[a.severity]}`)")
            st.markdown(a.detail)
            st.markdown(f"*Action:* {a.action}")
            st.caption(f"Sources: {a.sources}")
            st.divider()

    st.markdown("#### Spatial context (same map above)")
    st.caption("NDVI + GMW 2020: activate them in the map **layer panel**.")

    st.markdown("#### BRI Inventory (team local indices)")
    df_bri = pd.DataFrame({
        'Level': ['Low', 'Moderate', 'High', 'Critical'],
        'Hectares': [9550, 5247, 2601, 1325],
        'Description': [
            'Mangrove present, minimal risk',
            'Degraded or partial mangrove',
            'No mangrove, contaminated water',
            'Boomerang active: critical zone'
        ]
    })
    st.dataframe(df_bri, hide_index=True, use_container_width=True)
    st.metric("Total at Risk (moderate+)", "9,173 ha")

    st.info(
        "**Already integrated:** **rain + high sea level** coincidence (Marine model) + exposed coast (EO). "
        "**Next step:** **INOCAR** tide tables in the estuary (nautical precision), **Global Mangrove Watch** "
        "or **SERVIR/MANGLEE (Guayas)** layers in GEE to validate mangrove vs NDVI thresholds only."
    )

with st.expander("Time trends (2013–2024)", expanded=False):
    st.subheader("Time Trends (2013–2024)")

    col_e, col_f = st.columns(2)
    with col_e:
        st.markdown("**Chlorophyll in water (NDCI)**")
        st.markdown("Pollution signal drifts upward over the years")
        ndci_df = pd.DataFrame({
            'Year': [2016, 2018, 2019, 2020, 2021, 2022, 2023, 2024],
            'NDCI': [0.1589, 0.1175, 0.1582, 0.1331, 0.1419, 0.1618, 0.1699, 0.1870]
        })
        st.line_chart(ndci_df, x='Year', y='NDCI', color='#FF0000')
        st.metric("Trend", "+47% per decade")

    with col_f:
        st.markdown("**Healthy mangrove area**")
        st.markdown("Fairly stable with occasional stress events")
        manglar_df = pd.DataFrame({
            'Year': [2013,2014,2015,2016,2017,2018,2019,2020,2021,2023,2024],
            'Hectares': [5826,5822,5730,5758,5864,5082,5881,4822,5785,5888,5855]
        })
        st.line_chart(manglar_df, x='Year', y='Hectares', color='#228B22')
        st.metric("Trend", "-116.7 ha/year")

with st.expander("Extra: historical rain, variables & categorical map", expanded=False):
    st.subheader("Extra — team suggestions")
    st.markdown(
        "Quick integration: **flood percentage (proxy)** (same sidebar scenario), "
        "**historical rain vs forecast context**, **categorical layers** (dry forest, industrial, "
        "high-NDCI water) and **categorical variables** table."
    )

    fp_extra = flood_proxy_stats_cached(float(tide_pct), float(rain_stress_pct))
    z1, z2, z3 = st.columns(3)
    z1.metric(
        "% of ROI under flood proxy",
        f"{fp_extra.get('pct_roi_inundacion_proxy', 0)} %",
        help="Fraction of ROI rectangle covered by flood mask (current scenario).",
    )
    z2.metric(
        "% urban+soil flooded (proxy)",
        f"{fp_extra.get('pct_urbano_suelo_inundacion_proxy', 0)} %",
        help="Of urban+soil class area, how much falls under the proxy without nearby mangrove.",
    )
    z3.metric("Flood proxy area (ha)", f"{fp_extra.get('ha_inundacion_proxy', 0)}")
    st.caption("Same logic as main map and zone ranking; not a DEM hydraulic model.")

    arch, aerr, ctx = historical_rain_bundle()
    if aerr:
        st.warning(f"Archive Open-Meteo: {aerr}")
    if ctx.get("ratio_forecast_vs_typical_7d") is not None:
        st.metric(
            "7d forecast vs typical 7d windows (last 90d)",
            f"{ctx['ratio_forecast_vs_typical_7d']}× the rolling mean",
            help="7d forecast sum / mean of all 7-day rolling sums in the archive.",
        )
    st.caption(
        f"7d forecast sum: **{ctx.get('forecast_sum_7d_mm')}** mm · "
        f"Historical 7d window mean: **{ctx.get('hist_mean_7d_window_mm')}** mm · "
        f"Max 7d window: **{ctx.get('hist_max_7d_window_mm')}** mm"
    )
    if arch:
        st.markdown("##### Recent daily rain (Archive, ~90d)")
        dfh = pd.DataFrame(arch)
        st.line_chart(dfh, x="Fecha", y="mm", height=240)

    st.markdown("##### Categorical variables (used in maps and analysis)")
    df_cat = pd.DataFrame({
        "Variable": [
            "Land cover class (S2)",
            "Dry forest (proxy)",
            "Industrial / mixed fabric (proxy)",
            "Water + high NDCI (contamination proxy)",
            "GMW 2020 mangrove",
            "BRI risk level",
        ],
        "Type": ["Categorical 1–4", "Binary", "Binary", "Binary", "Binary", "Ordinal low→critical"],
        "Source": [
            "NDVI/MNDWI/NDBI",
            "Mid NDVI + low MNDWI",
            "NDBI + NDVI",
            "MNDWI water + NDCI",
            "Global Mangrove Watch",
            "Team indices / notebook",
        ],
    })
    st.dataframe(df_cat, hide_index=True, use_container_width=True)

    st.markdown("##### Categorical layers (same map above)")
    st.info(
        "Dry forest, industrial, water (high NDCI) and GMW: activate them in **Layers** next to the map."
    )
    st.caption(
        "Formal correlation (e.g. Pearson mangrove–NDCI): mentor metrics in notebook / report; "
        "here only operational context past rain vs next 7d."
    )

# --- FOOTER ---
st.divider()
col_g, col_h, col_i = st.columns(3)
col_g.info("**Population:** >3.3 million")
col_h.warning("**Extreme rainfall:** >70 mm/day")
col_i.error("**River tides:** Up to 5 meters")

st.markdown("---")
st.caption(
    "Boomerang Project | SpaceHACK 2026 | Track MCC | Sentinel-2 + Landsat 8 + GMW 2020 | "
    "GEE + Open-Meteo Forecast/Marine"
)
