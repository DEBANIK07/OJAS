"""
OJAS Energy Calculation Engine Service
Annual generation formula: capacity_kw × avg_GHI × PERFORMANCE_RATIO × 365
(pvlib / scipy are not used; removed from requirements.txt to keep the deploy lean.)
"""

import logging
import math
from typing import Dict, Any, List

logger = logging.getLogger("ojas.solar")

from backend.config import (
    DEFAULT_USABLE_AREA_FACTOR,
    ROOF_MATERIAL_USABLE_FACTORS,
    DEFAULT_PANEL_AREA_M2,
    DEFAULT_PANEL_WATTAGE,
    GRID_CO2_FACTOR_KG_KWH,
    ELECTRICITY_TARIFF_INR_KWH,
    DEFAULT_SYSTEM_DERATE,
    ALMM_PANEL_CATALOG,
    PERFORMANCE_RATIO,
    FALLBACK_GHI_KWH_M2_DAY,
    MONTHLY_GENERATION_FACTORS,
    COST_PER_KW_INR,
    COST_BREAKDOWN,
    SUBSIDY_RATE_FIRST_KW_INR,
    SUBSIDY_RATE_NEXT_KW_INR,
    SUBSIDY_FIRST_SLAB_KW,
    SUBSIDY_SECOND_SLAB_KW,
    SUBSIDY_MAX_INR,
)

# Backwards compatibility alias
USABLE_AREA_FACTOR = DEFAULT_USABLE_AREA_FACTOR


def calculate_energy_estimate(
    polygon_area_m2: float,
    latitude: float,
    longitude: float,
    elevation_m: float = 200.0,
    roof_tilt: float = 15.0,
    solar_azimuth: float = 180.0,
    bill_offset_percent: float = 100.0,
    panel_type: str = "monocrystalline",
    roof_material: str = "rcc",
    usable_area_factor: float = None,
    monthly_units: float = None,
) -> Dict[str, Any]:
    """
    Computes usable rooftop area, system capacity, and energy yield.

    :param polygon_area_m2: Total polygon area in square meters (Gross Area)
    :param latitude: Centroid latitude (degrees N)
    :param longitude: Centroid longitude (degrees E)
    :param elevation_m: Terrain elevation in meters ASL
    :param roof_tilt: Racking tilt angle in degrees (default 15° RCC)
    :param solar_azimuth: Roof solar facing azimuth (degrees, 180° = South)
    :param bill_offset_percent: User-adjustable bill offset slider (0-100%)
    :param panel_type: User-selected ALMM panel type key
    :param roof_material: Roof construction material ('rcc', 'tin', 'tile', 'asbestos', 'wood')
    :param usable_area_factor: Optional direct override of usable factor (0.1 to 1.0)
    :param monthly_units: Optional monthly electricity consumption (kWh/units). When provided,
        system capacity is capped at monthly_units / 120 kW so the system is never
        over-sized relative to the household's actual demand — matching the frontend rule.
    :returns: Comprehensive energy calculation result dictionary
    """
    # --------------------------------------------------------------------------
    # Step 1: Usable Rooftop Area Calculation with Material-Specific Derating
    # --------------------------------------------------------------------------
    polygon_area_m2 = max(1.0, float(polygon_area_m2))
    
    mat_key = str(roof_material).lower().strip()
    if usable_area_factor is not None and 0.1 <= float(usable_area_factor) <= 1.0:
        factor = float(usable_area_factor)
    else:
        factor = ROOF_MATERIAL_USABLE_FACTORS.get(mat_key, DEFAULT_USABLE_AREA_FACTOR)

    usable_area_m2 = round(polygon_area_m2 * factor, 2)
    gross_area_sqft = round(polygon_area_m2 * 10.7639, 1)
    usable_area_sqft = round(usable_area_m2 * 10.7639, 1)

    # --------------------------------------------------------------------------
    # Step 2: System Size (kWp) & Panel Layout Derivation
    # --------------------------------------------------------------------------
    panel_key = str(panel_type).lower().strip()
    panel_spec = ALMM_PANEL_CATALOG.get(panel_key, ALMM_PANEL_CATALOG["monocrystalline"])

    panel_area_m2 = panel_spec["area_m2"]
    panel_wattage_w = panel_spec["wattage_w"]
    panel_efficiency = panel_spec["efficiency"]
    panel_temp_coeff = panel_spec["temp_coeff"]

    num_panels = int(usable_area_m2 // panel_area_m2)
    system_capacity_kw = round((num_panels * panel_wattage_w) / 1000.0, 2)

    # Handle small rooftops gracefully (at least 1 panel if area >= 5m²)
    if num_panels == 0 and usable_area_m2 >= 5.0:
        num_panels = 1
        system_capacity_kw = round(panel_wattage_w / 1000.0, 2)

    # Cap by monthly electricity consumption when provided
    # Rule: ~120 units generated per kWp per month (Indian average)
    # This matches the frontend cap: capByUsage = electricityUnits / 120
    if monthly_units is not None and float(monthly_units) > 0:
        cap_by_usage_kw = float(monthly_units) / 120.0
        if system_capacity_kw > cap_by_usage_kw:
            system_capacity_kw = round(cap_by_usage_kw, 2)
            num_panels = max(1, int(system_capacity_kw * 1000 / panel_wattage_w))

    # --------------------------------------------------------------------------
    # Step 3: Phase 3 Generation Formula using 5-Year Weather Data
    # --------------------------------------------------------------------------
    lat = float(latitude)
    lng = float(longitude)
    elev = float(elevation_m)
    tilt = float(roof_tilt)
    azimuth = float(solar_azimuth)

    avg_ghi = FALLBACK_GHI_KWH_M2_DAY
    ghi_source = "fallback"
    try:
        from backend.services.weather_service import get_weather_history_data
        w = get_weather_history_data(lat, lng)
        avg_ghi = float(w["calculation_input"]["avg_ghi_kwh_m2_day"])
        ghi_source = "open-meteo"
    except Exception as exc:
        logger.warning("Open-Meteo failed (%s); using fallback GHI", exc)

    daily_generation_kwh = system_capacity_kw * avg_ghi * PERFORMANCE_RATIO
    annual_generation_kwh = round(daily_generation_kwh * 365, 1)

    base_monthly = annual_generation_kwh / 12.0
    monthly_kwh = [round(base_monthly * f, 1) for f in MONTHLY_GENERATION_FACTORS]

    # --------------------------------------------------------------------------
    # Step 4: Bill Offset & Financial / Environmental Impact Metrics
    # --------------------------------------------------------------------------
    offset_ratio = max(0.1, min(1.0, float(bill_offset_percent) / 100.0))
    adjusted_annual_generation_kwh = round(annual_generation_kwh * offset_ratio, 1)

    # PM Surya Ghar Subsidy (INR)
    subsidy_inr = calculate_pm_surya_ghar_subsidy(system_capacity_kw)

    # Annual Financial Savings
    annual_savings_inr = round(adjusted_annual_generation_kwh * ELECTRICITY_TARIFF_INR_KWH, 2)

    # Environmental CO2 Reduction (Tons per year)
    co2_offset_tons = round((adjusted_annual_generation_kwh * GRID_CO2_FACTOR_KG_KWH) / 1000.0, 2)

    # System Capex & Payback
    gross_system_cost_inr = round(system_capacity_kw * COST_PER_KW_INR, 2)
    net_system_cost_inr = max(0.0, gross_system_cost_inr - subsidy_inr)

    # Itemized cost breakdown using config shares; remainder goes to net-metering
    net_meter_share = max(0.0, 1.0 - sum(COST_BREAKDOWN.values()))
    cost_breakdown_inr = {
        "panels_inr":    round(gross_system_cost_inr * COST_BREAKDOWN["panels"], 2),
        "inverter_inr":  round(gross_system_cost_inr * COST_BREAKDOWN["inverter"], 2),
        "structure_inr": round(gross_system_cost_inr * COST_BREAKDOWN["structure"], 2),
        "bos_inr":       round(gross_system_cost_inr * COST_BREAKDOWN["bos"], 2),
        "net_meter_inr": round(gross_system_cost_inr * net_meter_share, 2),
    }
    
    payback_years = round(net_system_cost_inr / annual_savings_inr, 1) if annual_savings_inr > 0 else 0.0

    return {
        "status": "SUCCESS",
        "inputs": {
            "polygon_area_m2": polygon_area_m2,
            "latitude": lat,
            "longitude": lng,
            "elevation_m": elev,
            "roof_tilt": tilt,
            "solar_azimuth": azimuth,
            "bill_offset_percent": bill_offset_percent,
            "panel_type": panel_key,
            "roof_material": mat_key
        },
        "area_derivation": {
            "gross_area_m2": polygon_area_m2,
            "usable_area_m2": usable_area_m2,
            "gross_area_sqft": gross_area_sqft,
            "usable_area_sqft": usable_area_sqft,
            "usable_area_factor": factor,
            "usable_area_percent": round(factor * 100, 1),
            "perimeter_keepout_factor_percent": round((1.0 - factor) * 100, 1)
        },
        "system_sizing": {
            "panel_name": panel_spec["name"],
            "panel_wattage_w": panel_wattage_w,
            "panel_area_m2": panel_area_m2,
            "panel_efficiency_percent": round(panel_efficiency * 100, 1),
            "num_panels": num_panels,
            "system_capacity_kw": system_capacity_kw
        },
        "pvlib_simulation": {
            "annual_generation_kwh": annual_generation_kwh,
            "adjusted_generation_kwh": adjusted_annual_generation_kwh,
            "specific_yield_kwh_kwp": round(annual_generation_kwh / system_capacity_kw, 1) if system_capacity_kw > 0 else 0,
            "monthly_generation_kwh": monthly_kwh,
            "monthly_labels": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
            "avg_ghi_kwh_m2_day": round(avg_ghi, 3),
            "ghi_source": ghi_source
        },
        "financial_environmental_impact": {
            "electricity_tariff_inr_kwh": ELECTRICITY_TARIFF_INR_KWH,
            "annual_savings_inr": annual_savings_inr,
            "pm_surya_ghar_subsidy_inr": subsidy_inr,
            "gross_system_cost_inr": gross_system_cost_inr,
            "net_system_cost_inr": net_system_cost_inr,
            "cost_breakdown_inr": cost_breakdown_inr,
            "payback_period_years": payback_years,
            "co2_offset_tons_yr": co2_offset_tons
        }
    }


def calculate_pm_surya_ghar_subsidy(capacity_kw: float) -> float:
    """
    Calculates PM Surya Ghar: Muft Bijli Yojana Central Financial Assistance (CFA) subsidy.
    Rates are sourced from config.py so a government notification change needs only one edit.
    """
    cap = max(0.0, float(capacity_kw))
    if cap <= 0:
        return 0.0
    elif cap <= SUBSIDY_FIRST_SLAB_KW:
        return round(cap * SUBSIDY_RATE_FIRST_KW_INR, 2)
    elif cap <= SUBSIDY_SECOND_SLAB_KW:
        return round(
            SUBSIDY_FIRST_SLAB_KW * SUBSIDY_RATE_FIRST_KW_INR
            + (cap - SUBSIDY_FIRST_SLAB_KW) * SUBSIDY_RATE_NEXT_KW_INR,
            2,
        )
    else:
        return SUBSIDY_MAX_INR
