import ee
import altair as alt
import folium
import pandas as pd
import streamlit as st
from datetime import datetime
from streamlit_folium import st_folium

from boomerang_alerts import (
    SEVERITY_LABEL,
    alerts_to_dataframe_rows,
    build_alerts,
    fetch_marine_sea_level_hourly,
    fetch_open_meteo_precip_forecast,
)
from gee_layers import gmw_mangrove_mask_2020

st.set_page_config(page_title="Boomerang Dashboard", layout="wide", page_icon="🌊")

st.markdown(
    """
    <style>
        .main .block-container { padding-top: 1.2rem; }
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
    ee.Initialize(project='august-tower-470819-s6')
init_ee()

roi = ee.Geometry.Rectangle([-80.10, -2.30, -79.85, -1.98])

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

    classified = ee.Image(4)
    classified = classified.where(ndvi.gt(0.5), 1)
    classified = classified.where(ndbi.gt(0.0).And(ndvi.lt(0.3)), 2)
    classified = classified.where(mndwi.gt(0.2).And(ndvi.lt(0.3)), 3)
    classified = classified.clip(roi)

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
    return tiles

tiles = load_tiles()


def _layers_skip_none(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


def make_map(layers, height=500, layer_opacity=None):
    """layers: nombre -> url. layer_opacity: opcional dict nombre -> 0..1 para capas overlay."""
    m = folium.Map(location=[-2.15, -79.95], zoom_start=11, tiles='OpenStreetMap')
    for name, url in layers.items():
        if layer_opacity is not None:
            op = layer_opacity.get(name, 0.85)
        else:
            op = 1.0
        folium.TileLayer(
            tiles=url, attr='Google Earth Engine', name=name, overlay=True, opacity=op
        ).add_to(m)
    folium.LayerControl().add_to(m)
    return m


@st.cache_data(ttl=3600, show_spinner="Calculando simulación en Earth Engine…")
def tide_simulation_tiles(tide_pct: float):
    """
    Proxy visual de crecida: expansión morfológica desde el agua clasificada.
    tide_pct 0–100 controla el radio (~30–680 m). No es modelo hidráulico DEM;
    sirve para comunicar riesgo costero sin manglar cercano.
    """
    s2 = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
        .filterBounds(roi).filterDate('2023-06-01', '2024-12-31')
        .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 40))
        .median().clip(roi))

    ndvi = s2.normalizedDifference(['B8', 'B4']).rename('NDVI')
    mndwi = s2.normalizedDifference(['B3', 'B11']).rename('MNDWI')
    ndbi = s2.normalizedDifference(['B11', 'B8']).rename('NDBI')

    classified = ee.Image(4)
    classified = classified.where(ndvi.gt(0.5), 1)
    classified = classified.where(ndbi.gt(0.0).And(ndvi.lt(0.3)), 2)
    classified = classified.where(mndwi.gt(0.2).And(ndvi.lt(0.3)), 3)
    classified = classified.clip(roi)

    water = classified.eq(3)
    buffer_m = int(30 + (tide_pct / 100.0) * 650)
    expanded = water.focal_max(radius=buffer_m, units='meters')
    mang_near = classified.eq(1).focal_max(radius=280, units='meters')

    urban_expuesto = expanded.And(classified.eq(2)).And(mang_near.Not())
    suelo_expuesto = expanded.And(classified.eq(4)).And(mang_near.Not())
    inundacion = urban_expuesto.Or(suelo_expuesto).selfMask()

    return {
        'rgb': get_tile_url(s2, {'bands': ['B4', 'B3', 'B2'], 'min': 0, 'max': 3000}),
        'marea': get_tile_url(expanded.selfMask(), {'palette': ['1E90FF']}),
        'inundacion': get_tile_url(inundacion, {'palette': ['DC143C']}),
    }


@st.cache_data(ttl=1800, show_spinner="Sincronizando pronóstico, nivel del mar y motor de alertas…")
def alert_bundle_cached():
    daily, err = fetch_open_meteo_precip_forecast()
    marine, merr = fetch_marine_sea_level_hourly()
    alerts, metrics = build_alerts(daily, err, marine, merr)
    return alerts, metrics


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


# --- HEADER ---
st.title("Proyecto Boomerang")
st.markdown("**Manglares como barrera contra inundaciones en Greater Guayaquil**")
st.markdown("Sentinel-2 + Landsat 8 | Google Earth Engine | SpaceHACK 2026")
st.sidebar.markdown("### Boomerang Alert Engine")
st.sidebar.caption(
    "Combina GEE + lluvia + nivel del mar (Open-Meteo Forecast + Marine). "
    "No sustituye INAMHI / ECU911 / INOCAR."
)
st.sidebar.link_button(
    "Open-Meteo (lluvia)",
    "https://open-meteo.com/",
    help="API pública; atribución en informes.",
)
st.sidebar.link_button(
    "Open-Meteo Marine (marea/nivel mar)",
    "https://open-meteo.com/en/docs/marine-weather-api",
    help="sea_level_height_msl — proxy de marea en el Golfo.",
)
st.sidebar.link_button(
    "Global Mangrove Watch (paper + datos)",
    "https://doi.org/10.5281/zenodo.6894273",
    help="GMW v3/v4 — cita en slides; capa 2020 en GEE vía sat-io.",
)
if st.sidebar.button("Actualizar pronóstico y alertas", use_container_width=True):
    alert_bundle_cached.clear()
    st.rerun()

# --- METRICAS TOP ---
col1, col2, col3, col4 = st.columns(4)
col1.metric("Costa Protegida", "48.7%")
col2.metric("Costa Expuesta", "51.3%", delta="-9,367 ha", delta_color="inverse")
col3.metric("Expansion Urbana", "+11,247 ha", delta="2013-2024", delta_color="inverse")
col4.metric("Manglar Perdido", "-17,202 ha", delta="2013-2024", delta_color="inverse")

st.divider()

# --- TABS ---
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "Mapa de Uso de Suelo",
    "Proteccion Costera",
    "Centro de Alertas",
    "Tendencias Temporales",
    "Simulacion Mareas",
])

with tab1:
    st.subheader("Clasificacion de Uso de Suelo - 2024")
    st.markdown(
        "**Verde (S2)** = Manglar (umbrales NDVI/MNDWI) | **Rojo** = Urbano | **Azul** = Agua | **Dorado** = Suelo. "
        "Activa **GMW 2020** para comparar con el producto Global Mangrove Watch (~10 m, validación track)."
    )
    m1_layers = _layers_skip_none({
        'Sentinel-2 RGB': tiles['rgb'],
        'Clasificacion S2 (umbrales)': tiles['classified'],
        'GMW 2020 manglar (ref.)': tiles.get('gmw_2020'),
    })
    op1 = (
        {'Sentinel-2 RGB': 1.0, 'Clasificacion S2 (umbrales)': 0.92, 'GMW 2020 manglar (ref.)': 0.72}
        if tiles.get('gmw_2020')
        else None
    )
    m1 = make_map(m1_layers, layer_opacity=op1)
    st_folium(m1, height=500, use_container_width=True)
    st.caption(
        "GMW: Bunting et al., 2022 · capa anual 2020 `projects/sat-io/.../GMW_MNG_2020` · CC BY 4.0 · "
        "Diferencias vs S2: fechas, metodología SAR/óptico y resolución."
    )

    col_a, col_b = st.columns(2)
    with col_a:
        df_areas = pd.DataFrame({
            'Clase': ['Manglar/Vegetacion', 'Zona Urbana', 'Agua', 'Suelo/Agricultura'],
            'Hectareas': [48851, 26502, 4622, 116834],
            'Porcentaje': ['24.8%', '13.5%', '2.3%', '59.4%']
        })
        st.dataframe(df_areas, hide_index=True, use_container_width=True)
    with col_b:
        st.metric("NDVI Medio Manglar", "0.67")
        st.warning("Estado MODERADO - El manglar necesita atencion")

with tab2:
    st.subheader("Proteccion Costera contra Inundaciones")
    st.markdown("Franja de 500m del agua: **verde** = protegida, **rojo** = urbano expuesto, **naranja** = vulnerable")

    op2 = (
        {
            'RGB': 1.0,
            'Protegida (manglar S2)': 0.95,
            'Expuesta (urbano)': 0.95,
            'Vulnerable': 0.95,
            'GMW 2020 manglar': 0.62,
        }
        if tiles.get('gmw_2020')
        else None
    )
    m2 = make_map(_layers_skip_none({
        'RGB': tiles['rgb'],
        'Protegida (manglar S2)': tiles['protected'],
        'Expuesta (urbano)': tiles['exposed'],
        'Vulnerable': tiles['vulnerable'],
        'GMW 2020 manglar': tiles.get('gmw_2020'),
    }), layer_opacity=op2)
    st_folium(m2, height=500, use_container_width=True)

    col_c, col_d = st.columns(2)
    with col_c:
        st.metric("Costa Protegida", "8,888 ha (48.7%)")
        st.success("Zonas con barrera natural de manglar")
    with col_d:
        st.metric("Urbano Directo al Agua", "5,066 ha")
        st.error("Sin ninguna proteccion de manglar contra inundaciones")

with tab3:
    st.subheader("Centro de alertas Boomerang (prototipo)")
    st.markdown(
        "Motor de reglas: **GEE** (costa/manglar) + **lluvia** (Forecast API) + **nivel del mar** "
        "(Marine API, celda en el Golfo) + **índice 0–100** + **proxy económico** — track MCC."
    )

    alerts, am = alert_bundle_cached()

    ri = am.get("risk_index_0_100")
    if ri is not None:
        if ri >= 72:
            st.error(
                f"**Riesgo compuesto alto** — índice **{ri}/100** "
                "(lluvia + nivel del mar modelado + costa expuesta)."
            )
        elif ri >= 45:
            st.warning(f"**Riesgo compuesto moderado** — índice Boomerang **{ri}/100**.")
        else:
            st.success(f"**Riesgo compuesto acotado** — índice **{ri}/100** (seguir monitoreando).")
        st.progress(min(100, max(0, ri)) / 100.0, text=f"Índice Boomerang (0–100): {ri}")

    k1, k2, k3, k4 = st.columns(4)
    k1.metric(
        "Pronóstico 72 h (máx. mm/d)",
        am.get("max_precip_72h_mm") if am.get("max_precip_72h_mm") is not None else "—",
    )
    k2.metric(
        "Prob. máx. lluvia 72 h (%)",
        int(am["max_precip_prob_72h"]) if am.get("max_precip_prob_72h") is not None else "—",
    )
    k3.metric(
        "Lluvia acum. 7 d (mm)",
        am.get("precip_sum_7d_mm") if am.get("precip_sum_7d_mm") is not None else "—",
    )
    k4.metric(
        "Exposición económica (proxy MUSD/a)",
        f"{am['economic_exposure_proxy_million_usd']:.1f}" if am.get("economic_exposure_proxy_million_usd") is not None else "—",
    )

    s1, s2, s3 = st.columns(3)
    s1.metric(
        "Nivel mar máx. 72 h (m)",
        am.get("sea_level_max_72h_m") if am.get("sea_level_max_72h_m") is not None else "—",
        help="Open-Meteo Marine, celda Golfo; incluye marea modelada (~8 km).",
    )
    s2.metric(
        "Rango mareal 72 h (m)",
        am.get("sea_level_range_72h_m") if am.get("sea_level_range_72h_m") is not None else "—",
    )
    hw = am.get("marine_high_water_flag")
    s3.metric(
        "Marea alta relativa (72 h)",
        "Sí" if hw is True else ("No" if hw is False else "—"),
        help="Máx. en 72 h ≥ percentil 80 de la semana (proxy de pico de marea).",
    )
    st.caption(am.get("marine_point_label", ""))

    chart_rows = am.get("daily_precip_rows") or []
    if chart_rows:
        st.markdown("##### Pronóstico diario de lluvia (próximos días)")
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
                    alt.Tooltip("Dia", title="Día"),
                    alt.Tooltip("mm:Q", title="mm", format=".1f"),
                ],
            )
            .properties(height=260)
        )
        st.altair_chart(precip_chart, use_container_width=True)

    st.caption(
        f"Última sincronización UTC: **{am.get('updated_utc', '—')}** · "
        "Lluvia: Forecast API · Nivel del mar: Marine API · Índice: heurística demo (no oficial)."
    )

    st.markdown("#### Cola de alertas (ordenadas por severidad)")
    rows = alerts_to_dataframe_rows(alerts)
    df_alerts = pd.DataFrame(rows)[["Severidad", "Titulo", "Accion"]]
    st.dataframe(df_alerts, hide_index=True, use_container_width=True, height=min(320, 60 + len(alerts) * 38))

    with st.expander("Ver texto completo de cada alerta (para demo / pitch)"):
        for i, a in enumerate(alerts, 1):
            st.markdown(f"**{i}. {a.title}** (`{SEVERITY_LABEL[a.severity]}`)")
            st.markdown(a.detail)
            st.markdown(f"*Accion:* {a.action}")
            st.caption(f"Fuentes: {a.sources}")
            st.divider()

    st.markdown("#### Mapa de contexto: NDVI, GMW 2020 y zonas de estudio")
    op3 = (
        {'RGB': 1.0, 'NDVI (salud vegetacion)': 0.88, 'GMW 2020 manglar': 0.68}
        if tiles.get('gmw_2020')
        else None
    )
    m3 = make_map(_layers_skip_none({
        'RGB': tiles['rgb'],
        'NDVI (salud vegetacion)': tiles['ndvi'],
        'GMW 2020 manglar': tiles.get('gmw_2020'),
    }), layer_opacity=op3)
    st_folium(m3, height=420, use_container_width=True)

    st.markdown("#### Inventario BRI (indices locales del equipo)")
    df_bri = pd.DataFrame({
        'Nivel': ['Bajo', 'Moderado', 'Alto', 'Critico'],
        'Hectareas': [9550, 5247, 2601, 1325],
        'Descripcion': [
            'Manglar presente, riesgo minimo',
            'Manglar degradado o parcial',
            'Sin manglar, agua contaminada',
            'Boomerang activo: zona critica'
        ]
    })
    st.dataframe(df_bri, hide_index=True, use_container_width=True)
    st.metric("Total en Riesgo (moderado+)", "9,173 ha")

    st.info(
        "**Ya integrado:** coincidencia **lluvia + nivel del mar alto** (modelo Marine) + costa expuesta (EO). "
        "**Siguiente paso:** tablas **INOCAR** en el estuario (precisión náutica), capas **Global Mangrove Watch** "
        "o **SERVIR/MANGLEE (Guayas)** en GEE para validar manglar frente a solo NDVI por umbrales."
    )

with tab4:
    st.subheader("Tendencias Temporales (2013-2024)")

    col_e, col_f = st.columns(2)
    with col_e:
        st.markdown("**Clorofila en el Agua (NDCI)**")
        st.markdown("La contaminacion sube cada anio")
        ndci_df = pd.DataFrame({
            'Anio': [2016, 2018, 2019, 2020, 2021, 2022, 2023, 2024],
            'NDCI': [0.1589, 0.1175, 0.1582, 0.1331, 0.1419, 0.1618, 0.1699, 0.1870]
        })
        st.line_chart(ndci_df, x='Anio', y='NDCI', color='#FF0000')
        st.metric("Tendencia", "+47% por decada")

    with col_f:
        st.markdown("**Area de Manglar Sano**")
        st.markdown("Estable pero con eventos de estres")
        manglar_df = pd.DataFrame({
            'Anio': [2013,2014,2015,2016,2017,2018,2019,2020,2021,2023,2024],
            'Hectareas': [5826,5822,5730,5758,5864,5082,5881,4822,5785,5888,5855]
        })
        st.line_chart(manglar_df, x='Anio', y='Hectareas', color='#228B22')
        st.metric("Tendencia", "-116.7 ha/anio")

with tab5:
    st.subheader("Simulacion de marea e inundacion costera")
    st.markdown(
        "Ajusta el deslizador para ver un **proxy** de como la franja inundada crece cuando sube "
        "la marea (expansion desde el agua detectada por satelite). En **rojo**: suelo urbano o "
        "descubierto **sin manglar en un buffer de ~280 m**, es decir, costa mas expuesta al "
        "avance del agua. No es un modelo hidraulico con DEM; es una narrativa clara para el hackathon."
    )
    tide_pct = st.slider(
        "Nivel de marea simulado (crece la expansion desde el agua)",
        min_value=0,
        max_value=100,
        value=45,
        help="0 = minima expansion; 100 = expansion maxima (~680 m desde la mascara de agua).",
    )
    buffer_m = int(30 + (tide_pct / 100.0) * 650)
    st.caption(f"Expansion aproximada desde la linea de agua: **{buffer_m} m** (morfologia, no batimetria).")

    t = tide_simulation_tiles(float(tide_pct))
    m5 = make_map(
        {
            "Sentinel-2 RGB": t["rgb"],
            "Zona inundada (proxy marea)": t["marea"],
            "Expuesto sin manglar cercano": t["inundacion"],
        },
        layer_opacity={
            "Sentinel-2 RGB": 0.72,
            "Zona inundada (proxy marea)": 0.45,
            "Expuesto sin manglar cercano": 0.78,
        },
    )
    st_folium(m5, height=520, use_container_width=True)
    st.info(
        "Los manglares actuan como franja amortiguadora: donde hay vegetacion costera cerca, "
        "esta capa enfatiza menos el riesgo (pixel no marcado en rojo). Restaurar manglar reduce "
        "la exposicion directa urbano-agua."
    )

# --- FOOTER ---
st.divider()
col_g, col_h, col_i = st.columns(3)
col_g.info("**Poblacion:** >3.3 millones")
col_h.warning("**Lluvia extrema:** >70 mm/dia")
col_i.error("**Mareas:** Hasta 5 metros")

st.markdown("---")
st.caption(
    "Proyecto Boomerang | SpaceHACK 2026 | Track MCC | Sentinel-2 + Landsat 8 + GMW 2020 | "
    "GEE + Open-Meteo Forecast/Marine"
)
