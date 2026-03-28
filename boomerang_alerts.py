"""
Motor de alertas Boomerang — prototipo para SpaceHACK 2026 (Track MCC).

Combina:
  - Métricas estáticas derivadas de EO (Greater Guayaquil) alineadas al notebook/dashboard.
  - Pronóstico de lluvia (Open-Meteo Forecast API).
  - Nivel del mar horario con mareas modeladas (Open-Meteo Marine API, ``sea_level_height_msl``);
    punto de celda marina en el Golfo — no reemplaza tablas de marea INOCAR para navegación.

No sustituye sistemas oficiales de INAMHI/ECU911/INOCAR; demuestra reglas trazables y datos abiertos.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import IntEnum
from typing import Any, List, Optional, Tuple

import numpy as np
import pandas as pd

# --- ROI centroide (Greater Guayaquil) — mismo orden que dashboard / notebook ---
LAT_GYE = -2.15
LON_GYE = -79.95

# Celda marina (Golfo de Guayaquil): el modelo ~8 km devuelve null en el centro urbano;
# coordenadas ligeramente al SW sobre agua — proxy de marea para el estuario.
MARINE_LAT = -2.42
MARINE_LON = -79.98

# Métricas EO (calibradas con el pipeline Sentinel-2 / Landsat del equipo; actualizar si recalculáis)
PCT_COAST_PROTECTED = 48.7
PCT_COAST_EXPOSED = 51.3
HA_URBAN_DIRECT_WATER = 5066.0
HA_COASTAL_EXPOSED_TOTAL = 9367.0  # urbano + suelo sin manglar en franja costera (orden de magnitud)
HA_RISK_MODERATE_PLUS = 9173.0

# Umbrales alineados al problem statement del track (lluvia extrema >70 mm/día)
MM_EXTREME_DAY = 70.0
MM_HEAVY_DAY = 40.0

# Proxy económico: daño potencial anualizado expuesto (orden de magnitud; literatura inundación costera tropical)
# Disclaimer obligatorio en UI — no es valuación de seguros.
USD_FLOOD_DAMAGE_PER_HA_EXPOSED_YEAR = 4200.0


class Severity(IntEnum):
    INFO = 1
    ADVISORY = 2
    WATCH = 3
    WARNING = 4


SEVERITY_LABEL = {
    Severity.INFO: "Info",
    Severity.ADVISORY: "Advisory",
    Severity.WATCH: "Watch",
    Severity.WARNING: "Warning",
}


@dataclass(frozen=True)
class AlertItem:
    severity: Severity
    title: str
    detail: str
    action: str
    sources: str


# Misma ventana que el pronóstico de lluvia; si es menor, hay días con lluvia pero sin horas marinas.
MARINE_FORECAST_DAYS = 14


def fetch_marine_sea_level_hourly(
    lat: float = MARINE_LAT,
    lon: float = MARINE_LON,
    forecast_days: int = MARINE_FORECAST_DAYS,
) -> Tuple[Optional[dict], Optional[str]]:
    """
    Nivel del mar respecto a MSL (incluye marea + efectos); resolución ~8 km.
    Ver documentación Open-Meteo Marine — no es almanaque náutico.
    """
    url = (
        f"https://marine-api.open-meteo.com/v1/marine?"
        f"latitude={lat}&longitude={lon}"
        f"&hourly=sea_level_height_msl"
        f"&timezone=America/Guayaquil"
        f"&forecast_days={forecast_days}"
        f"&cell_selection=sea"
    )
    try:
        with urllib.request.urlopen(url, timeout=22) as resp:
            data = json.loads(resp.read().decode())
        hourly = data.get("hourly")
        if not hourly or "sea_level_height_msl" not in hourly:
            return None, "Marine API response missing hourly sea level"
        vals = [v for v in hourly["sea_level_height_msl"] if v is not None]
        if len(vals) < 8:
            return (
                None,
                "Sea level not available at this grid cell (use offshore cell; in production: link INOCAR).",
            )
        return hourly, None
    except Exception as e:
        return None, str(e)


def marine_metrics_from_hourly(hourly: Optional[dict]) -> dict[str, Any]:
    """Máx. 72 h, rango de marea, comparación vs percentil semanal (marea alta relativa)."""
    if not hourly:
        return {}
    raw = hourly.get("sea_level_height_msl") or []
    vals: List[float] = []
    for v in raw:
        if v is None:
            continue
        vals.append(float(v))
    if len(vals) < 8:
        return {}
    s = pd.Series(vals)
    p50 = float(s.quantile(0.50))
    p80 = float(s.quantile(0.80))
    p95 = float(s.quantile(0.95))
    first72 = vals[:72] if len(vals) >= 72 else vals
    max72 = max(first72)
    min72 = min(first72)
    range72 = max72 - min72
    high_water = max72 >= p80
    denom = max(p95 - p50, 1e-6)
    sea_stress = float(min(1.0, max(0.0, (max72 - p50) / denom)))
    return {
        "sea_level_max_72h_m": round(max72, 2),
        "sea_level_min_72h_m": round(min72, 2),
        "sea_level_range_72h_m": round(range72, 2),
        "sea_level_p80_week_m": round(p80, 2),
        "marine_high_water_flag": high_water,
        "sea_stress_0_1": sea_stress,
    }


def _api_time_to_local_date_key(t: Any) -> Optional[str]:
    """Extrae YYYY-MM-DD del instante que devuelve Open-Meteo (timezone ya aplicada en la petición)."""
    ts = str(t).strip()
    if not ts:
        return None
    # "2025-03-31T00:00" o con Z / offset
    if len(ts) >= 10 and ts[4] == "-" and ts[7] == "-":
        return ts[:10]
    return None


def sea_level_daily_stats_for_iso(
    hourly: Optional[dict],
    date_iso: str,
) -> Optional[dict[str, float]]:
    """
    Máx / mín / rango de ``sea_level_height_msl`` (m) para un día ``YYYY-MM-DD``
    en las series horarias del Marine API (misma zona que el motor de alertas).
    """
    if not hourly or not date_iso:
        return None
    date_key = date_iso.strip()[:10]
    if len(date_key) != 10:
        return None
    times = hourly.get("time") or []
    levels = hourly.get("sea_level_height_msl") or []
    day_levels: List[float] = []
    for t, v in zip(times, levels):
        if v is None:
            continue
        tkey = _api_time_to_local_date_key(t)
        if tkey == date_key:
            day_levels.append(float(v))
    if not day_levels:
        return None
    mx = max(day_levels)
    mn = min(day_levels)
    return {
        "max_m": mx,
        "min_m": mn,
        "range_m": mx - mn,
        "n_hours": float(len(day_levels)),
    }


def fetch_open_meteo_archive_precipitation(
    past_days: int = 90,
    lat: float = LAT_GYE,
    lon: float = LON_GYE,
) -> Tuple[Optional[List[dict[str, Any]]], Optional[str]]:
    """
    Lluvia diaria histórica (Open-Meteo Archive API) para correlación / contexto vs pronóstico.
    """
    end = date.today()
    start = end - timedelta(days=past_days)
    url = (
        f"https://archive-api.open-meteo.com/v1/archive?"
        f"latitude={lat}&longitude={lon}"
        f"&start_date={start.isoformat()}&end_date={end.isoformat()}"
        f"&daily=precipitation_sum&timezone=America/Guayaquil"
    )
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            data = json.loads(resp.read().decode())
        d = data.get("daily") or {}
        times = d.get("time") or []
        vals = d.get("precipitation_sum") or []
        rows = []
        for i, t in enumerate(times):
            v = float(vals[i]) if i < len(vals) and vals[i] is not None else 0.0
            rows.append({"Fecha": t, "mm": round(v, 2)})
        return rows, None
    except Exception as e:
        return None, str(e)


def forecast_vs_history_context(
    archive_rows: Optional[list[dict]],
    forecast_daily: Optional[dict],
) -> dict[str, Any]:
    """
    Compara acumulado 7d del pronóstico con la distribución de ventanas de 7d en el histórico reciente.
    (Proxy de 'correlación' operativa para el equipo — no es estudio estadístico formal.)
    """
    out: dict[str, Any] = {
        "forecast_sum_7d_mm": None,
        "hist_mean_7d_window_mm": None,
        "hist_max_7d_window_mm": None,
        "ratio_forecast_vs_typical_7d": None,
    }
    if not archive_rows or len(archive_rows) < 7:
        return out
    arr = np.array([r["mm"] for r in archive_rows], dtype=float)
    windows = [float(arr[i : i + 7].sum()) for i in range(len(arr) - 6)]
    out["hist_mean_7d_window_mm"] = round(float(np.mean(windows)), 2)
    out["hist_max_7d_window_mm"] = round(float(np.max(windows)), 2)

    if not forecast_daily:
        return out
    fvals = forecast_daily.get("precipitation_sum") or []
    fc7 = sum(float(fvals[i]) for i in range(min(7, len(fvals)))) if fvals else 0.0
    out["forecast_sum_7d_mm"] = round(fc7, 2)
    m = out["hist_mean_7d_window_mm"]
    if m and m > 1e-6:
        out["ratio_forecast_vs_typical_7d"] = round(fc7 / m, 2)
    return out


def fetch_open_meteo_precip_forecast(
    lat: float = LAT_GYE,
    lon: float = LON_GYE,
    forecast_days: int = MARINE_FORECAST_DAYS,
) -> Tuple[Optional[dict], Optional[str]]:
    """Devuelve (payload daily con precipitation_sum y precipitation_probability_max, error_message)."""
    url = (
        f"https://api.open-meteo.com/v1/forecast?"
        f"latitude={lat}&longitude={lon}"
        f"&daily=precipitation_sum,precipitation_probability_max"
        f"&timezone=America/Guayaquil"
        f"&forecast_days={forecast_days}"
    )
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        daily = data.get("daily")
        if not daily:
            return None, "Open-Meteo response missing daily block"
        return daily, None
    except Exception as e:
        return None, str(e)


def _max_precip_next_days(daily: dict, n_days: int) -> Tuple[float, List[Tuple[str, float]]]:
    times = daily.get("time") or []
    vals = daily.get("precipitation_sum") or []
    pairs: List[Tuple[str, float]] = []
    for i, t in enumerate(times[:n_days]):
        v = float(vals[i]) if i < len(vals) and vals[i] is not None else 0.0
        pairs.append((t, v))
    if not pairs:
        return 0.0, []
    mx = max(p[1] for p in pairs)
    return mx, pairs


def _max_precip_prob_next_days(daily: dict, n_days: int) -> float:
    times = daily.get("time") or []
    probs = daily.get("precipitation_probability_max") or []
    best = 0.0
    for i in range(min(n_days, len(times))):
        if i < len(probs) and probs[i] is not None:
            best = max(best, float(probs[i]))
    return best


def _sum_precip_next_days(daily: dict, n_days: int) -> float:
    vals = daily.get("precipitation_sum") or []
    s = 0.0
    for i in range(min(n_days, len(vals))):
        if vals[i] is not None:
            s += float(vals[i])
    return s


def daily_precip_dataframe_rows(daily: Optional[dict]) -> List[dict]:
    """Filas para st.bar_chart: fecha + mm acumulados por día."""
    if not daily:
        return []
    times = daily.get("time") or []
    vals = daily.get("precipitation_sum") or []
    rows = []
    for i, t in enumerate(times):
        v = float(vals[i]) if i < len(vals) and vals[i] is not None else 0.0
        rows.append({"Fecha": t, "mm": round(v, 1)})
    return rows


def peak_precipitation_day_72h(rows: List[dict]) -> Tuple[Optional[str], float]:
    """
    Fecha (YYYY-MM-DD) y mm del día con más lluvia en los primeros 3 días del pronóstico
    (misma ventana que usa el índice Boomerang para la parte de lluvia).
    """
    if not rows:
        return None, 0.0
    slice3 = rows[:3]
    best = max(slice3, key=lambda r: float(r.get("mm", 0) or 0))
    return best.get("Fecha"), float(best.get("mm", 0) or 0)


def compute_risk_index_100(
    mx3_mm: float,
    pct_coast_exposed: float,
    sea_stress_0_1: float = 0.0,
) -> int:
    """
    Índice compuesto 0–100: lluvia (72 h) + exposición costera EO + estrés de nivel del mar (0–1).
    Heurística para prototipo; no es índice institucional.
    """
    rain = min(48.0, (mx3_mm / max(MM_EXTREME_DAY, 1.0)) * 48.0)
    coast = 35.0 * (pct_coast_exposed / 100.0)
    sea = 17.0 * max(0.0, min(1.0, sea_stress_0_1))
    return int(min(100, max(0, round(rain + coast + sea))))


def estimate_economic_exposure_proxy_usd(ha_exposed: float) -> float:
    """Orden de magnitud de daños potenciales anualizados (proxy, no actuarial)."""
    return max(0.0, ha_exposed * USD_FLOOD_DAMAGE_PER_HA_EXPOSED_YEAR)


def build_alerts(
    daily: Optional[dict],
    forecast_error: Optional[str],
    marine_hourly: Optional[dict] = None,
    marine_error: Optional[str] = None,
) -> Tuple[List[AlertItem], dict]:
    """
    Construye lista de alertas y un dict de métricas para el dashboard.
    """
    metrics: dict[str, Any] = {
        "updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ"),
        "forecast_ok": daily is not None,
        "forecast_error": forecast_error,
        "marine_ok": marine_hourly is not None,
        "marine_error": marine_error,
        "marine_hourly": marine_hourly,
        "marine_point_label": f"Marine grid {MARINE_LAT}, {MARINE_LON} (Gulf, ~8 km resolution)",
        "max_precip_72h_mm": None,
        "max_precip_7d_mm": None,
        "max_precip_prob_72h": None,
        "precip_sum_7d_mm": None,
        "risk_index_0_100": None,
        "economic_exposure_proxy_million_usd": None,
        "daily_precip_rows": [],
        "sea_level_max_72h_m": None,
        "sea_level_range_72h_m": None,
        "marine_high_water_flag": None,
        "sea_stress_0_1": None,
    }

    alerts: List[AlertItem] = []

    # Siempre: contexto de costa (EO) — responde research questions del track sobre % protegido/expuesto
    alerts.append(
        AlertItem(
            severity=Severity.WATCH if PCT_COAST_EXPOSED >= 50 else Severity.ADVISORY,
            title="Boomerang coastal index (Earth observation)",
            detail=(
                f"About {PCT_COAST_EXPOSED:.1f}% of the analysed coastal strip has little or no mangrove buffer "
                f"within 500 m of the water (Sentinel-2 + NDVI/MNDWI/NDBI rules). "
                f"Urban fabric directly facing water: ~{HA_URBAN_DIRECT_WATER:,.0f} ha."
            ),
            action="Prioritize restoration and green corridors along the red stretches on the coastal protection map.",
            sources="Google Earth Engine — COPERNICUS/S2_SR_HARMONIZED, water mask + 500 m buffer",
        )
    )

    proxy_usd = estimate_economic_exposure_proxy_usd(HA_COASTAL_EXPOSED_TOTAL)
    metrics["economic_exposure_proxy_million_usd"] = proxy_usd / 1e6
    alerts.append(
        AlertItem(
            severity=Severity.INFO,
            title="Economic exposure (illustrative proxy)",
            detail=(
                f"Roughly ${proxy_usd / 1e6:.1f} M USD/year in potential flood-related losses linked to "
                f"~{HA_COASTAL_EXPOSED_TOTAL:,.0f} ha of exposed coast (conservative demo calibration; "
                "not an insurance quote or cadastral study)."
            ),
            action="Validate with local land-registry data and INAMHI hydraulic models before policy use.",
            sources="Proxy = exposed hectares × literature factor for tropical coastal flooding (tune in report)",
        )
    )

    if daily is None:
        mm_only = marine_metrics_from_hourly(marine_hourly) if marine_hourly else {}
        sea_s = float(mm_only.get("sea_stress_0_1", 0.0) or 0.0)
        for k, v in mm_only.items():
            if k in metrics:
                metrics[k] = v
        metrics["risk_index_0_100"] = compute_risk_index_100(0.0, PCT_COAST_EXPOSED, sea_s)
        alerts.append(
            AlertItem(
                severity=Severity.ADVISORY,
                title="Weather forecast unavailable",
                detail=forecast_error or "No data",
                action="Try again later; satellite layers still work.",
                sources="Open-Meteo API",
            )
        )
        if marine_hourly is None and marine_error:
            alerts.append(
                AlertItem(
                    severity=Severity.INFO,
                    title="Sea level (Marine model)",
                    detail=marine_error,
                    action="In production: plug in INOCAR tide tables or a regional buoy.",
                    sources="Open-Meteo Marine API",
                )
            )
        return alerts, metrics

    mx3, pairs3 = _max_precip_next_days(daily, 3)
    mx7, _ = _max_precip_next_days(daily, 7)
    prob72 = _max_precip_prob_next_days(daily, 3)
    sum7 = _sum_precip_next_days(daily, 7)
    metrics["max_precip_72h_mm"] = round(mx3, 1)
    metrics["max_precip_7d_mm"] = round(mx7, 1)
    metrics["max_precip_prob_72h"] = round(prob72, 0)
    metrics["precip_sum_7d_mm"] = round(sum7, 1)
    mm = marine_metrics_from_hourly(marine_hourly)
    sea_stress = float(mm.get("sea_stress_0_1", 0.0) or 0.0)
    for k, v in mm.items():
        if k in metrics:
            metrics[k] = v
    metrics["risk_index_0_100"] = compute_risk_index_100(mx3, PCT_COAST_EXPOSED, sea_stress)
    metrics["daily_precip_rows"] = daily_precip_dataframe_rows(daily)

    if marine_hourly is None and marine_error:
        alerts.append(
            AlertItem(
                severity=Severity.INFO,
                title="Sea level: no data this run",
                detail=marine_error,
                action="Retry; in operations link INOCAR or Gulf buoys.",
                sources="Open-Meteo Marine API",
            )
        )

    # Coincidencia lluvia + marea alta relativa + costa expuesta (track: lluvia + marea)
    if (
        marine_hourly is not None
        and mm.get("marine_high_water_flag")
        and mx3 >= 20
        and PCT_COAST_EXPOSED >= 45
    ):
        alerts.append(
            AlertItem(
                severity=Severity.WARNING if mx3 >= MM_HEAVY_DAY else Severity.WATCH,
                title="Overlap: rain + high sea level (estuary proxy)",
                detail=(
                    f"Peak daily rain ~{mx3:.0f} mm in 72 h together with a high sea-level phase in the marine model "
                    f"(max {metrics.get('sea_level_max_72h_m')} m). With long stretches of coast without mangrove, "
                    "flood risk rises in low areas and drains toward the estuary."
                ),
                action="Flag coastal neighbourhoods; check NDVI and coastal protection maps.",
                sources="Open-Meteo Forecast + Marine + EO index",
            )
        )

    # Puente entre “calma” y lluvia fuerte: costa expuesta + lluvia moderada (25–40 mm/d)
    if MM_HEAVY_DAY > mx3 >= 25 and PCT_COAST_EXPOSED >= 50:
        alerts.append(
            AlertItem(
                severity=Severity.WATCH,
                title="Boomerang compound scenario (rain + exposed coast)",
                detail=(
                    f"Up to ~{mx3:.0f} mm in one day (72 h window) with ~{PCT_COAST_EXPOSED:.0f}% of coast lacking mangrove "
                    "inside the buffer. Stress on drains and low areas with little natural buffer."
                ),
                action="Prioritise outreach in high-BRI neighbourhoods; cross-check coastal protection map.",
                sources="Open-Meteo + EO coastal index (Greater Guayaquil)",
            )
        )

    # Escenario combinado: lluvia fuerte + costa ya expuesta (narrativa track: drenaje + marea/lluvia)
    if mx3 >= MM_EXTREME_DAY:
        alerts.append(
            AlertItem(
                severity=Severity.WARNING,
                title="Condición de lluvia extrema prevista (72 h)",
                detail=(
                    f"Máximo diario previsto ~{mx3:.0f} mm en la ventana de 3 días (umbral track: {MM_EXTREME_DAY:.0f} mm/día). "
                    f"Con ~{PCT_COAST_EXPOSED:.0f}% de costa sin manglar, el riesgo de falla de drenaje urbano es elevado."
                ),
                action="Activar protocolos de comunidad: evitar zonas bajas, monitorear canales y alertas oficiales INAMHI.",
                sources="Open-Meteo forecast + índice costero EO",
            )
        )
    elif mx3 >= MM_HEAVY_DAY:
        alerts.append(
            AlertItem(
                severity=Severity.WATCH,
                title="Heavy rain expected (72 h)",
                detail=f"Peak daily rain ~{mx3:.0f} mm. Combined with an exposed coast, fluvial–coastal flood risk rises.",
                action="Early heads-up for high-BRI areas; review NDVI / degraded mangrove maps.",
                sources="Open-Meteo forecast + EO metrics",
            )
        )
    else:
        alerts.append(
            AlertItem(
                severity=Severity.INFO,
                title="Moderate weather window (72 h)",
                detail=f"Peak daily rain ~{mx3:.1f} mm in 72 h. Keep an eye on the coastal index and mangrove health.",
                action="Use the tide simulation panel for community awareness sessions.",
                sources="Open-Meteo forecast",
            )
        )

    alerts.sort(key=lambda a: (-int(a.severity), a.title))
    return alerts, metrics


def alerts_to_dataframe_rows(alerts: List[AlertItem]) -> List[dict]:
    rows = []
    for a in alerts:
        rows.append(
            {
                "Severity": SEVERITY_LABEL[a.severity],
                "Title": a.title,
                "Detail": a.detail,
                "Action": a.action,
                "Sources": a.sources,
            }
        )
    return rows
