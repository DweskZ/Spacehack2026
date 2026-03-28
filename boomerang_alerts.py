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
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any, List, Optional, Tuple

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
    Severity.INFO: "Informativo",
    Severity.ADVISORY: "Preventivo",
    Severity.WATCH: "Vigilancia",
    Severity.WARNING: "Alerta alta",
}


@dataclass(frozen=True)
class AlertItem:
    severity: Severity
    title: str
    detail: str
    action: str
    sources: str


def fetch_marine_sea_level_hourly(
    lat: float = MARINE_LAT,
    lon: float = MARINE_LON,
    forecast_days: int = 7,
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
            return None, "Respuesta Marine sin hourly"
        vals = [v for v in hourly["sea_level_height_msl"] if v is not None]
        if len(vals) < 8:
            return (
                None,
                "Nivel del mar no disponible en este pixel (usar celda marina; en producción: INOCAR).",
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


def fetch_open_meteo_precip_forecast(
    lat: float = LAT_GYE,
    lon: float = LON_GYE,
    forecast_days: int = 7,
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
            return None, "Respuesta Open-Meteo sin bloque daily"
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
        "marine_point_label": f"Marino {MARINE_LAT}, {MARINE_LON} (Golfo, ~8 km)",
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
            title="Índice costero Boomerang (EO)",
            detail=(
                f"~{PCT_COAST_EXPOSED:.1f}% de la franja costera analizada queda sin protección de manglar "
                f"en el buffer de 500 m (Sentinel-2 + reglas NDVI/MNDWI/NDBI). "
                f"Urbano directo al agua: ~{HA_URBAN_DIRECT_WATER:,.0f} ha."
            ),
            action="Priorizar restauración y corredores verdes en tramos rojos del mapa de protección costera.",
            sources="Google Earth Engine — COPERNICUS/S2_SR_HARMONIZED, máscara agua + buffer 500 m",
        )
    )

    proxy_usd = estimate_economic_exposure_proxy_usd(HA_COASTAL_EXPOSED_TOTAL)
    metrics["economic_exposure_proxy_million_usd"] = proxy_usd / 1e6
    alerts.append(
        AlertItem(
            severity=Severity.INFO,
            title="Exposición económica (proxy)",
            detail=(
                f"Orden de magnitud ~${proxy_usd / 1e6:.1f} M USD/año en daños potenciales asociados a "
                f"~{HA_COASTAL_EXPOSED_TOTAL:,.0f} ha costeros expuestos (calibración conservadora para demo; "
                "no es póliza ni estudio de suelo)."
            ),
            action="Validar con datos locales de catastro y modelos hidráulicos INAMHI para cifras de política pública.",
            sources="Proxy derivado de hectáreas expuestas × factor literatura inundación costera (ajustar en informe)",
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
                title="Pronóstico meteorológico no disponible",
                detail=forecast_error or "Sin datos",
                action="Reintentar más tarde; el motor sigue activo con capas satelitales.",
                sources="Open-Meteo API",
            )
        )
        if marine_hourly is None and marine_error:
            alerts.append(
                AlertItem(
                    severity=Severity.INFO,
                    title="Nivel del mar (modelo Marine)",
                    detail=marine_error,
                    action="En producción: integrar tablas INOCAR o boya regional.",
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
                title="Nivel del mar: sin datos en esta corrida",
                detail=marine_error,
                action="Reintentar; en operación real enlazar INOCAR / boyas del Golfo.",
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
                title="Coincidencia: lluvia + nivel del mar elevado (proxy estuario)",
                detail=(
                    f"Lluvia máx. diaria ~{mx3:.0f} mm en 72 h junto a fase de nivel del mar alto en el modelo marino "
                    f"(máx. {metrics.get('sea_level_max_72h_m')} m). Con mucha costa sin manglar, sube el riesgo de "
                    "inundación en bajos y drenajes hacia el estuario."
                ),
                action="Priorizar aviso a comunidades costeras; revisar mapas NDVI y protección costera.",
                sources="Open-Meteo Forecast + Marine + índice EO",
            )
        )

    # Puente entre “calma” y lluvia fuerte: costa expuesta + lluvia moderada (25–40 mm/d)
    if MM_HEAVY_DAY > mx3 >= 25 and PCT_COAST_EXPOSED >= 50:
        alerts.append(
            AlertItem(
                severity=Severity.WATCH,
                title="Escenario compuesto Boomerang (lluvia + costa expuesta)",
                detail=(
                    f"Hasta ~{mx3:.0f} mm max. en un día (72 h) con ~{PCT_COAST_EXPOSED:.0f}% de costa sin manglar "
                    "en buffer. Tensión en drenajes y bajos sin amortiguación de manglar."
                ),
                action="Priorizar mensajes en barrios BRI alto; cruzar con mapa de protección costera.",
                sources="Open-Meteo + índice costero EO (Greater Guayaquil)",
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
                title="Lluvia intensa prevista (72 h)",
                detail=f"Máximo diario previsto ~{mx3:.0f} mm. Superposición con costa expuesta incrementa riesgo de inundación fluvial-costera.",
                action="Pre-advertencia a comunidades en BRI alto; revisar mapas NDVI/manglar degradado.",
                sources="Open-Meteo forecast + métricas EO",
            )
        )
    else:
        alerts.append(
            AlertItem(
                severity=Severity.INFO,
                title="Ventana meteorológica moderada (72 h)",
                detail=f"Máximo diario previsto ~{mx3:.1f} mm en 72 h. Mantener vigilancia del índice costero y salud del manglar.",
                action="Usar el tab de simulación de marea para talleres de conciencia ciudadana.",
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
                "Severidad": SEVERITY_LABEL[a.severity],
                "Titulo": a.title,
                "Detalle": a.detail,
                "Accion": a.action,
                "Fuentes": a.sources,
            }
        )
    return rows
