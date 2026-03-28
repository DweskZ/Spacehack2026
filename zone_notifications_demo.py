"""
Demo didáctica: profundidad de agua ilustrativa por barrio + simulación del mapa.
Opcional: comparar con un día del pronóstico Open-Meteo (mm/día → referencia visual).
Figura humana ~170 cm (referencia visual, no modelo hidráulico).
"""

from __future__ import annotations

import html
from datetime import datetime

from boomerang_alerts import sea_level_daily_stats_for_iso

# Factores ilustrativos por barrio (misma escena → más o menos agua según relieve típico / cercanía a estero).
# No son datos de campo; solo para que cambie el mensaje al cambiar de recuadro.
ZONE_DEPTH_FACTOR: dict[str, float] = {
    "Mapasingue / Flor de Bastión": 0.94,
    "Sauces / Alborada (norte)": 0.88,
    "Samborondón / vía a la costa": 0.9,
    "Centro / Olmedo": 1.02,
    "Urdesa / Kennedy": 0.96,
    "Puerto Santa Ana / Malecón 2000": 1.06,
    "Estero Salado (oeste)": 1.34,
    "Guasmo": 1.22,
    "Cristo del Consuelo": 1.28,
    "Suburbio / Febres Cordero": 1.26,
    "Trinidad de Dios / Sur": 1.18,
    "Sur / Guasmo sur": 1.2,
    "Orillas estero (norte)": 1.14,
    "Este del estuario (límite ROI)": 1.2,
}


def format_day_en(iso_date: str | None) -> str:
    """Short label like «28 Mar» (English, for demo UI)."""
    if not iso_date:
        return "—"
    try:
        d = datetime.strptime(iso_date[:10], "%Y-%m-%d")
    except ValueError:
        return str(iso_date)
    months = (
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    )
    return f"{d.day} {months[d.month - 1]}"


# Por debajo de esto, el modelo no prevé lluvia útil: no mostramos “inundación” ficticia.
MM_FORECAST_NEGLIGIBLE = 0.5

# Por encima de esto (mm/día), la marea en la tarjeta «Pronóstico del día» aporta al 100 %.
# Por debajo, se atenúa para no mostrar decenas de cm por marea cuando casi no llueve ese día.
FORECAST_TIDE_BLEND_FULL_MM = 25.0


def water_depth_cm_from_tide_range(zona: str, range_m: float) -> float:
    """
    Amplitud de marea del día (máx − mín del nivel del mar en el modelo) → cm ilustrativos.
    Coeficientes moderados para no inflar la figura frente a lluvia medida en mm.
    """
    r = max(0.0, float(range_m))
    if r < 1e-9:
        return 0.0
    base = r * 36.0
    f = ZONE_DEPTH_FACTOR.get(zona, 1.0)
    return max(0.0, min(58.0, base * f * 0.5))


def tide_cm_weighted_for_forecast_figure(zona: str, range_m: float, mm: float) -> float:
    """
    Marea ilustrativa para la figura del pronóstico: si el día tiene poca lluvia,
    la aportación mareal se reduce (evita sumar ~80 cm de marea con 3–5 mm).
    """
    r = max(0.0, float(range_m))
    if r < 1e-9:
        return 0.0
    m = max(0.0, float(mm))
    if m < MM_FORECAST_NEGLIGIBLE:
        return 0.0
    raw = water_depth_cm_from_tide_range(zona, r)
    w = min(1.0, m / FORECAST_TIDE_BLEND_FULL_MM)
    return raw * w


def water_depth_cm_from_forecast_mm(zona: str, mm: float) -> float:
    """
    Traduce mm de lluvia diaria del pronóstico a una profundidad ilustrativa (cm).
    Factor < 1: la lluvia acumulada no equivale a esa lámina en calle (escorrentía, drenaje).
    """
    m = max(0.0, float(mm))
    if m < MM_FORECAST_NEGLIGIBLE:
        return 0.0
    # Orden de magnitud ~lluvia intensa urbana → pocos cm en la metáfora, no mm≈cm.
    base = m * 0.62
    f = ZONE_DEPTH_FACTOR.get(zona, 1.0)
    depth = base * f
    return max(0.0, min(95.0, depth))


# En la figura de simulación, la “marea” sola no llena calle: sin estrés de lluvia solo aporta un hilo.
# r=0 → factor ~0.07 sobre el término mareal; r=1 → término mareal al 100 %.
SIM_TIDE_ALONE_FACTOR = 0.07
SIM_TIDE_WITH_RAIN_BLEND = 0.93


def water_depth_cm_demo(zona: str, tide_pct: float, rain_stress_pct: float) -> float:
    """
    Profundidad en cm para la demo (rejilla marea × lluvia + factor barrio).
    La componente mareal en la metáfora de calle queda **acoplada** al estrés de lluvia:
    marea baja–media sin lluvia no debe mostrar decenas de cm (solo charcos / piso mojado).
    """
    t = max(0.0, min(1.0, float(tide_pct) / 100.0))
    r = max(0.0, min(1.0, float(rain_stress_pct) / 100.0))
    tide_w = SIM_TIDE_ALONE_FACTOR + SIM_TIDE_WITH_RAIN_BLEND * r
    base = t * 58.0 * tide_w + r * 42.0
    f = ZONE_DEPTH_FACTOR.get(zona, 1.0)
    depth = base * f
    return max(0.0, min(125.0, depth))


def human_depth_phrase_cm(total_cm: float) -> str:
    """Same verbal scale for simulation and forecast (ankles, knees, etc.)."""
    t = float(total_cm)
    if t < 0.5:
        return "almost no standing water in this illustration"
    return body_zone_label_en(t)


def body_zone_label_en(depth_cm: float) -> str:
    """Plain-language description by approximate depth."""
    d = float(depth_cm)
    if d < 12:
        return "mostly wet pavement"
    if d < 28:
        return "around ankle depth"
    if d < 50:
        return "below the knees"
    if d < 75:
        return "about knee height"
    if d < 95:
        return "about mid-thigh"
    return "near waist level or higher (unsafe to walk; electrical hazard)"


def svg_person_water(depth_cm: float, adult_cm: float = 170.0) -> str:
    """
    Silueta simple; 1 unidad SVG ≈ 1 cm en vertical (pies en y=170).
    """
    d = max(0.0, min(float(depth_cm), adult_cm))
    ground = 170.0
    water_top = ground - d

    # Etiqueta posición: bajo la línea del agua si hay espacio
    label_y = min(ground - 4.0, water_top + 16.0)

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 188" width="210" height="395" role="img" aria-label="Reference person {adult_cm:.0f} cm tall; approximate water level {d:.0f} cm">
  <rect x="0" y="0" width="100" height="188" fill="#f7f9fc"/>
  <rect x="0" y="{water_top:.2f}" width="100" height="{ground - water_top:.2f}" fill="rgba(30,144,255,0.38)"/>
  <line x1="0" y1="{water_top:.2f}" x2="100" y2="{water_top:.2f}" stroke="#187bcd" stroke-width="2"/>
  <text x="4" y="{label_y:.1f}" font-size="11" fill="#024" font-family="system-ui,sans-serif">≈ {d:.0f} cm</text>
  <circle cx="50" cy="30" r="14" fill="none" stroke="#1a1a1a" stroke-width="2.5"/>
  <line x1="50" y1="44" x2="50" y2="118" stroke="#1a1a1a" stroke-width="3"/>
  <line x1="50" y1="62" x2="26" y2="92" stroke="#1a1a1a" stroke-width="2.5"/>
  <line x1="50" y1="62" x2="74" y2="92" stroke="#1a1a1a" stroke-width="2.5"/>
  <line x1="50" y1="118" x2="36" y2="170" stroke="#1a1a1a" stroke-width="3"/>
  <line x1="50" y1="118" x2="64" y2="170" stroke="#1a1a1a" stroke-width="3"/>
  <line x1="8" y1="170" x2="92" y2="170" stroke="#999" stroke-width="1.5"/>
  <text x="52" y="184" font-size="10" fill="#555" font-family="system-ui,sans-serif">≈ {adult_cm:.0f} cm de altura</text>
</svg>"""


def _svg_forecast_dry(mm: float, adult_cm: float = 170.0) -> str:
    """Sin agua en la referencia (sin lluvia útil ni marea relevante en la metáfora)."""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 188" width="210" height="395" role="img" aria-label="Sin acumulación ilustrativa">
  <rect x="0" y="0" width="100" height="188" fill="#f8fafc"/>
  <circle cx="50" cy="30" r="14" fill="none" stroke="#1a1a1a" stroke-width="2.5"/>
  <line x1="50" y1="44" x2="50" y2="118" stroke="#1a1a1a" stroke-width="3"/>
  <line x1="50" y1="62" x2="26" y2="92" stroke="#1a1a1a" stroke-width="2.5"/>
  <line x1="50" y1="62" x2="74" y2="92" stroke="#1a1a1a" stroke-width="2.5"/>
  <line x1="50" y1="118" x2="36" y2="170" stroke="#1a1a1a" stroke-width="3"/>
  <line x1="50" y1="118" x2="64" y2="170" stroke="#1a1a1a" stroke-width="3"/>
  <line x1="8" y1="170" x2="92" y2="170" stroke="#94a3b8" stroke-width="1.5" stroke-dasharray="3,3"/>
  <text x="4" y="155" font-size="10" fill="#64748b" font-family="system-ui,sans-serif">Little or no water</text>
  <text x="52" y="184" font-size="10" fill="#555" font-family="system-ui,sans-serif">≈ {adult_cm:.0f} cm tall</text>
</svg>"""


def svg_forecast_stacked_water(
    rain_cm: float,
    tide_cm: float,
    total_cm: float,
    mm: float,
    adult_cm: float = 170.0,
) -> str:
    """
    Agua apilada: abajo marea (verde azulado), arriba lluvia (azul). Misma escala 1 cm ≈ 1 unidad Y.
    """
    total_cm = max(0.0, min(float(total_cm), adult_cm))
    rain_cm = max(0.0, float(rain_cm))
    tide_cm = max(0.0, float(tide_cm))
    if total_cm < 0.5:
        return _svg_forecast_dry(mm, adult_cm)

    ground = 170.0
    water_top = ground - total_cm
    label_y = min(ground - 4.0, water_top + 14.0)
    tide_y0 = ground - tide_cm
    rain_y0 = ground - tide_cm - rain_cm
    aria = "Rain and tide illustration" if tide_cm > 0.05 else "Forecast rain illustration"
    foot = (
        f"~{adult_cm:.0f} cm figure · tide below · rain above"
        if tide_cm > 0.05
        else f"~{adult_cm:.0f} cm figure · forecast rain"
    )

    rects = ""
    if tide_cm > 0.05:
        rects += f'<rect x="0" y="{tide_y0:.2f}" width="100" height="{tide_cm:.2f}" fill="rgba(13,148,136,0.45)"/>'
    if rain_cm > 0.05:
        rects += f'<rect x="0" y="{rain_y0:.2f}" width="100" height="{rain_cm:.2f}" fill="rgba(30,144,255,0.38)"/>'
    if not rects:
        rects = f'<rect x="0" y="{water_top:.2f}" width="100" height="{total_cm:.2f}" fill="rgba(30,144,255,0.38)"/>'

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 188" width="210" height="395" role="img" aria-label="{aria}">
  <rect x="0" y="0" width="100" height="188" fill="#f7f9fc"/>
  {rects}
  <line x1="0" y1="{water_top:.2f}" x2="100" y2="{water_top:.2f}" stroke="#187bcd" stroke-width="2"/>
  <text x="4" y="{label_y:.1f}" font-size="10" fill="#024" font-family="system-ui,sans-serif">≈ {total_cm:.0f} cm</text>
  <circle cx="50" cy="30" r="14" fill="none" stroke="#1a1a1a" stroke-width="2.5"/>
  <line x1="50" y1="44" x2="50" y2="118" stroke="#1a1a1a" stroke-width="3"/>
  <line x1="50" y1="62" x2="26" y2="92" stroke="#1a1a1a" stroke-width="2.5"/>
  <line x1="50" y1="62" x2="74" y2="92" stroke="#1a1a1a" stroke-width="2.5"/>
  <line x1="50" y1="118" x2="36" y2="170" stroke="#1a1a1a" stroke-width="3"/>
  <line x1="50" y1="118" x2="64" y2="170" stroke="#1a1a1a" stroke-width="3"/>
  <line x1="8" y1="170" x2="92" y2="170" stroke="#999" stroke-width="1.5"/>
  <text x="52" y="184" font-size="9" fill="#555" font-family="system-ui,sans-serif">{foot}</text>
</svg>"""


def _card_simulation(
    zona: str,
    tide_pct: float,
    rain_stress_pct: float,
    tide_label: str = "",
    rain_label: str = "",
) -> str:
    d = water_depth_cm_demo(zona, tide_pct, rain_stress_pct)
    z_esc = html.escape(zona)
    lab = human_depth_phrase_cm(d)
    svg = _svg_forecast_dry(0.0) if d < 0.5 else svg_person_water(d)
    t_esc = html.escape(tide_label) if tide_label else ""
    r_esc = html.escape(rain_label) if rain_label else ""
    escenario = ""
    if t_esc and r_esc:
        escenario = f"""<p style="margin:0 0 10px 0;font-size:0.88rem;color:#334155;line-height:1.45;">
    <span style="display:block;"><b>Tide</b> (simulation): {t_esc}</span>
    <span style="display:block;margin-top:4px;"><b>Rain</b> (simulation): {r_esc}</span>
    </p>"""
    return f"""<div style="display:flex;align-items:flex-start;gap:1.25rem;flex-wrap:wrap;border:1px solid #e2e8f0;border-radius:12px;padding:14px 16px;background:#fff;width:100%;box-sizing:border-box;">
  <div style="flex-shrink:0;">{svg}</div>
  <div style="flex:1;min-width:220px;line-height:1.5;font-size:0.98rem;color:#111;">
    <p style="margin:0 0 6px 0;font-size:0.82rem;color:#2563eb;font-weight:600;">Map scenario</p>
    <p style="margin:0 0 8px 0;"><strong>{z_esc}</strong></p>
    {escenario}
    <p style="margin:0 0 6px 0;font-size:1.05rem;line-height:1.35;">{(
        f'<strong>≈ {d:.0f} cm</strong> — <em>{html.escape(lab)}</em>.' if d >= 0.5
        else f'<strong>No standing water</strong> in this illustration — <em>{html.escape(lab)}</em>.'
    )}</p>
    <p style="margin:0;color:#5c5c5c;font-size:0.9rem;">Adjust tide and rain in the sidebar (simulation).</p>
  </div>
</div>"""


def _card_forecast_day(zona: str, iso: str, mm: float, marine_hourly: dict | None) -> str:
    z_esc = html.escape(zona)
    dia_esc = html.escape(format_day_en(iso))
    stats = sea_level_daily_stats_for_iso(marine_hourly, iso[:10]) if iso else None

    rain_cm = water_depth_cm_from_forecast_mm(zona, mm)
    tide_cm = 0.0
    if stats:
        tide_cm = tide_cm_weighted_for_forecast_figure(
            zona, float(stats["range_m"]), float(mm)
        )

    total_cm = min(125.0, rain_cm + tide_cm)
    lab = human_depth_phrase_cm(total_cm)
    svg = svg_forecast_stacked_water(rain_cm, tide_cm, total_cm, mm)

    if stats:
        extra = (
            " Forecast rain and tide; on a dry day the tide contributes less to the figure "
            "(so we do not show high water from tide alone when there is almost no rain)."
        )
    else:
        extra = ""

    if total_cm < 0.5:
        cuerpo = (
            f"<p style=\"margin:0 0 6px 0;font-size:1.05rem;line-height:1.35;\"><strong>≈ 0 cm</strong> — <em>{lab}</em>.</p>"
            f"<p style=\"margin:0;color:#444;font-size:0.92rem;\">Day <strong>{dia_esc}</strong>.{extra}</p>"
        )
    else:
        cuerpo = (
            f"<p style=\"margin:0 0 6px 0;font-size:1.05rem;line-height:1.35;\"><strong>≈ {total_cm:.0f} cm</strong> — <em>{lab}</em>.</p>"
            f"<p style=\"margin:0;color:#444;font-size:0.92rem;\">Day <strong>{dia_esc}</strong>.{extra}</p>"
        )

    return f"""<div style="display:flex;align-items:flex-start;gap:1.25rem;flex-wrap:wrap;border:1px solid #bbf7d0;border-radius:12px;padding:14px 16px;background:#f0fdf4;width:100%;box-sizing:border-box;">
  <div style="flex-shrink:0;">{svg}</div>
  <div style="flex:1;min-width:220px;line-height:1.5;font-size:0.98rem;color:#111;">
    <p style="margin:0 0 6px 0;font-size:0.82rem;color:#15803d;font-weight:600;">Today’s forecast</p>
    <p style="margin:0 0 8px 0;"><strong>{z_esc}</strong></p>
    {cuerpo}
  </div>
</div>"""


def citizen_flood_demo_html(zona: str, tide_pct: float, rain_stress_pct: float) -> str:
    """Solo bloque de simulación (compatibilidad)."""
    return _card_simulation(zona, tide_pct, rain_stress_pct)


def citizen_flood_cards_pair(
    zona: str,
    tide_pct: float,
    rain_stress_pct: float,
    forecast_iso: str | None,
    forecast_mm: float | None,
    marine_hourly: dict | None = None,
    tide_label: str = "",
    rain_label: str = "",
) -> tuple[str, str | None]:
    """
    Devuelve (HTML simulación, HTML pronóstico o None) para usar con st.columns(2) en Streamlit.
    ``marine_hourly`` alimenta la marea del día (Open-Meteo Marine).
    ``tide_label`` / ``rain_label`` describen el escenario cualitativo (misma barra lateral que el mapa).
    """
    a = _card_simulation(zona, tide_pct, rain_stress_pct, tide_label, rain_label)
    if not forecast_iso or forecast_mm is None:
        return a, None
    b = _card_forecast_day(zona, forecast_iso, float(forecast_mm), marine_hourly)
    return a, b
