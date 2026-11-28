"""
OJAS Backend Configuration & Engineering Constants
PM Surya Ghar Rooftop Solar Assessment Platform

Everything that is a deployment setting, a secret, or an assumption that may change
(tariff, cost per kW, subsidy slabs, performance ratio, default location, external API
URLs) lives in this file. Deployment settings and secrets are read from environment
variables (or a local .env file in development), so the same code runs on a laptop and
on the server without edits. See .env.example for the full list of variables.
"""

import os

try:  # Optional: load a local .env file in development (pip install python-dotenv)
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ==============================================================================
# Environment helpers
# ==============================================================================
def _env_str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return float(default)
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"Environment variable {name}={raw!r} is not a valid number")


def _env_list(name: str, default: str = "") -> list:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


# ==============================================================================
# Deployment settings
# ==============================================================================
APP_ENV: str = _env_str("APP_ENV", "development").lower()   # "development" | "production"
IS_PRODUCTION: bool = APP_ENV == "production"

SERVER_HOST: str = _env_str("HOST", "0.0.0.0")
SERVER_PORT: int = int(_env_float("PORT", 8000))             # Render/Railway/Fly set PORT
AUTO_RELOAD: bool = not IS_PRODUCTION                        # never auto-reload in production

# The backend serves the frontend itself, so production is same-origin and needs no CORS
# origins. In development the Live Server port (5501) is allowed by default.
_DEV_CORS_ORIGINS = (
    "http://localhost:8000,http://127.0.0.1:8000,"
    "http://localhost:5501,http://127.0.0.1:5501"
)
CORS_ORIGINS: list = _env_list("CORS_ORIGINS", "" if IS_PRODUCTION else _DEV_CORS_ORIGINS)

# ==============================================================================
# Secrets (never hardcode these; set them in the environment / hosting dashboard)
# ==============================================================================
GEOAPIFY_API_KEY: str = _env_str("GEOAPIFY_API_KEY")
DATABASE_URL: str = _env_str("DATABASE_URL")                 # for storing vendor quote requests

# ==============================================================================
# External services
# ==============================================================================
OPEN_METEO_ARCHIVE_URL: str = _env_str(
    "OPEN_METEO_ARCHIVE_URL", "https://archive-api.open-meteo.com/v1/archive")
OPEN_ELEVATION_URL: str = _env_str(
    "OPEN_ELEVATION_URL", "https://api.open-elevation.com/api/v1/lookup")
GEOAPIFY_REVERSE_URL: str = _env_str(
    "GEOAPIFY_REVERSE_URL", "https://api.geoapify.com/v1/geocode/reverse")
HTTP_TIMEOUT_S: float = _env_float("HTTP_TIMEOUT_S", 15)

WEATHER_HISTORY_YEARS: int = 10   # years of daily data fetched from Open-Meteo
WEATHER_AVERAGE_YEARS: int = 5    # most recent years averaged for the generation calculation

# ==============================================================================
# Default location & roof geometry (used only when a request omits them)
# ==============================================================================
DEFAULT_LAT: float = _env_float("DEFAULT_LAT", 22.5726)            # Kolkata
DEFAULT_LNG: float = _env_float("DEFAULT_LNG", 88.3639)
DEFAULT_ELEVATION_M: float = _env_float("DEFAULT_ELEVATION_M", 15.0)
DEFAULT_ROOF_TILT_DEG: float = 15.0       # racking tilt for an RCC flat terrace
DEFAULT_SOLAR_AZIMUTH_DEG: float = 180.0  # 180 degrees = due south
DEFAULT_AREA_SQFT: float = _env_float("DEFAULT_AREA_SQFT", 650.0)  # quick-readout default

# Unit conversions
SQFT_PER_M2: float = 10.7639
M2_PER_SQFT: float = 0.092903

# ==============================================================================
# Step 1: Usable Roof Area Utilization Factors by Construction Material
# Factors account for 1.5-3 ft fire/perimeter setbacks, maintenance walkways, mumty/water tanks, and roof geometry
# ==============================================================================
DEFAULT_USABLE_AREA_FACTOR: float = 0.75
USABLE_AREA_FACTOR: float = DEFAULT_USABLE_AREA_FACTOR

ROOF_MATERIAL_USABLE_FACTORS = {
    "rcc": 0.75,       # RCC flat terrace (perimeter setbacks, mumty, water tanks)
    "tin": 0.80,       # Galvanized tin/metal sheet (high continuous usable surface)
    "tile": 0.65,      # Clay/Mangalore tiles (pitch, ridges, valleys, fragile mounting)
    "asbestos": 0.70,  # Asbestos/fiber sheet (purlin mounting spacing constraints)
    "wood": 0.60       # Wood/truss framing (structural load limits)
}

# ==============================================================================
# Step 2: Standard Module Specifications (ALMM Reference Table)
# ==============================================================================
DEFAULT_PANEL_AREA_M2: float = 1.7   # m² per standard panel
DEFAULT_PANEL_WATTAGE: int = 400     # Watts per panel
MIN_AREA_FOR_ONE_PANEL_M2: float = 5.0   # small roofs still get 1 panel at or above this usable area

# ==============================================================================
# Energy yield assumptions
# ==============================================================================
# Single source of truth for system losses (inverter, wiring, dirt, temperature).
PERFORMANCE_RATIO: float = _env_float("PERFORMANCE_RATIO", 0.75)
# Backwards-compatible alias: solar_engine.py still imports this name. It was 0.84 and
# disagreed with the 0.75 the engine actually used; both names now share one value.
DEFAULT_SYSTEM_DERATE: float = PERFORMANCE_RATIO

# Used only if the live Open-Meteo request fails (kWh/m²/day, roughly Kolkata average).
FALLBACK_GHI_KWH_M2_DAY: float = _env_float("FALLBACK_GHI_KWH_M2_DAY", 5.14)

# Seasonal shape applied to the annual generation (Jan..Dec).
MONTHLY_GENERATION_FACTORS = [1.05, 1.10, 1.15, 1.12, 0.95, 0.75,
                              0.70, 0.75, 0.85, 1.05, 1.10, 1.08]

# ==============================================================================
# Environmental & Financial Constants (India Grid Context)
# ==============================================================================
GRID_CO2_FACTOR_KG_KWH: float = _env_float("GRID_CO2_FACTOR_KG_KWH", 0.82)       # kg CO2 avoided per solar kWh
ELECTRICITY_TARIFF_INR_KWH: float = _env_float("ELECTRICITY_TARIFF_INR_KWH", 7.0)  # avg residential tariff (₹/kWh)
ANNUAL_TARIFF_ESCALATION: float = _env_float("ANNUAL_TARIFF_ESCALATION", 0.04)     # used by the 25-year ROI chart

# System cost (₹ per kW installed, before subsidy)
COST_PER_KW_INR: float = _env_float("COST_PER_KW_INR", 48000.0)
COST_PER_KW_EXTRA_INR: float = _env_float("COST_PER_KW_EXTRA_INR", 4000.0)   # surcharge for harder roofs
COST_EXTRA_MATERIALS = ("tile", "wood")                                       # materials that get the surcharge

# Share of gross system cost per line item. Whatever is left over is the net-metering cost.
COST_BREAKDOWN = {
    "panels": 0.58,
    "inverter": 0.18,
    "structure": 0.11,
    "bos": 0.07,
}

# PM Surya Ghar: Muft Bijli Yojana Central Financial Assistance (CFA).
# These follow government notifications and can change, so keep them in one place.
SUBSIDY_RATE_FIRST_KW_INR: float = 30000.0    # ₹/kW for the first 2 kW
SUBSIDY_RATE_NEXT_KW_INR: float = 18000.0     # ₹/kW for the 3rd kW
SUBSIDY_FIRST_SLAB_KW: float = 2.0
SUBSIDY_SECOND_SLAB_KW: float = 3.0
SUBSIDY_MAX_INR: float = 78000.0              # cap at 3 kW and above

# ==============================================================================
# Placeholder environmental values (replace with measured data when available)
# ==============================================================================
DEFAULT_WIND_MS: float = 2.5
DEFAULT_ALBEDO: float = 0.20
DEFAULT_CLEAR_SKY_DAYS: int = 290
DEFAULT_HUMIDITY_PCT: int = 60

# ==============================================================================
# ALMM Panel Reference Catalog
# ==============================================================================
ALMM_PANEL_CATALOG = {
    "monocrystalline": {
        "name": "Mono-PERC High Efficiency (ALMM Approved)",
        "wattage_w": 400,
        "area_m2": 1.7,
        "efficiency": 0.205,
        "temp_coeff": -0.0035,  # %/°C
    },
    "polycrystalline": {
        "name": "Polycrystalline Standard (ALMM Approved)",
        "wattage_w": 335,
        "area_m2": 1.7,
        "efficiency": 0.175,
        "temp_coeff": -0.0039,
    },
    "bifacial": {
        "name": "Bifacial N-Type TOPCon Dual-Glass",
        "wattage_w": 440,
        "area_m2": 1.8,
        "efficiency": 0.220,
        "temp_coeff": -0.0030,
    }
}

# ==============================================================================
# 10-Year Historical Climate Datasets (2016-2025) for Representative Indian Districts
# GHI values in kWh/m²/day, Temperatures in °C
# Used only as an offline fallback when the live Open-Meteo request fails.
# ==============================================================================
HISTORICAL_YEARS = ["2016", "2017", "2018", "2019", "2020", "2021", "2022", "2023", "2024", "2025"]

DISTRICT_CLIMATE_DATA = {
    "kolkata": {
        "name": "Kolkata",
        "state": "West Bengal",
        "lat": 22.5726,
        "lng": 88.3639,
        "ghi": [4.95, 5.08, 5.12, 4.90, 5.02, 5.15, 5.05, 5.18, 5.14, 5.21],
        "temp": [26.8, 27.1, 27.4, 27.0, 27.3, 27.6, 27.5, 27.8, 27.7, 28.0],
        "sunny_days": 284,
        "dust_index": "Moderate",
        "panel_temp_loss_pct": 3.9
    },
    "delhi": {
        "name": "New Delhi",
        "state": "Delhi NCR",
        "lat": 28.6139,
        "lng": 77.2090,
        "ghi": [5.25, 5.34, 5.40, 5.20, 5.30, 5.38, 5.35, 5.42, 5.40, 5.46],
        "temp": [25.1, 25.4, 25.8, 25.3, 25.6, 26.0, 25.9, 26.3, 26.2, 26.5],
        "sunny_days": 305,
        "dust_index": "High (Seasonal)",
        "panel_temp_loss_pct": 4.2
    },
    "jaipur": {
        "name": "Jaipur",
        "state": "Rajasthan",
        "lat": 26.9124,
        "lng": 75.7873,
        "ghi": [5.68, 5.75, 5.82, 5.62, 5.70, 5.80, 5.78, 5.86, 5.83, 5.90],
        "temp": [25.8, 26.1, 26.5, 26.0, 26.4, 26.8, 26.7, 27.1, 27.0, 27.3],
        "sunny_days": 322,
        "dust_index": "High",
        "panel_temp_loss_pct": 4.5
    },
    "nagpur": {
        "name": "Nagpur",
        "state": "Maharashtra",
        "lat": 21.1458,
        "lng": 79.0882,
        "ghi": [5.38, 5.46, 5.52, 5.32, 5.42, 5.50, 5.48, 5.56, 5.52, 5.58],
        "temp": [27.0, 27.3, 27.7, 27.2, 27.5, 27.9, 27.8, 28.2, 28.1, 28.4],
        "sunny_days": 308,
        "dust_index": "Low-Moderate",
        "panel_temp_loss_pct": 4.4
    },
    "bengaluru": {
        "name": "Bengaluru",
        "state": "Karnataka",
        "lat": 12.9716,
        "lng": 77.5946,
        "ghi": [5.42, 5.48, 5.55, 5.36, 5.45, 5.52, 5.50, 5.58, 5.54, 5.60],
        "temp": [24.0, 24.3, 24.6, 24.2, 24.5, 24.8, 24.7, 25.1, 25.0, 25.3],
        "sunny_days": 298,
        "dust_index": "Low",
        "panel_temp_loss_pct": 3.1
    },
    "mumbai": {
        "name": "Mumbai",
        "state": "Maharashtra",
        "lat": 19.0760,
        "lng": 72.8777,
        "ghi": [5.05, 5.12, 5.18, 4.98, 5.08, 5.16, 5.12, 5.20, 5.18, 5.24],
        "temp": [27.5, 27.8, 28.1, 27.7, 28.0, 28.3, 28.2, 28.6, 28.5, 28.8],
        "sunny_days": 288,
        "dust_index": "Low (Coastal)",
        "panel_temp_loss_pct": 3.8
    },
    "ahmedabad": {
        "name": "Ahmedabad",
        "state": "Gujarat",
        "lat": 23.0225,
        "lng": 72.5714,
        "ghi": [5.55, 5.62, 5.70, 5.50, 5.58, 5.68, 5.65, 5.74, 5.70, 5.78],
        "temp": [27.2, 27.5, 27.9, 27.4, 27.7, 28.1, 28.0, 28.4, 28.3, 28.6],
        "sunny_days": 315,
        "dust_index": "Moderate",
        "panel_temp_loss_pct": 4.3
    },
    "hyderabad": {
        "name": "Hyderabad",
        "state": "Telangana",
        "lat": 17.3850,
        "lng": 78.4867,
        "ghi": [5.32, 5.40, 5.48, 5.28, 5.36, 5.45, 5.42, 5.50, 5.46, 5.52],
        "temp": [26.6, 26.9, 27.3, 26.8, 27.1, 27.5, 27.4, 27.8, 27.7, 28.0],
        "sunny_days": 302,
        "dust_index": "Moderate",
        "panel_temp_loss_pct": 4.0
    },
    "chennai": {
        "name": "Chennai",
        "state": "Tamil Nadu",
        "lat": 13.0827,
        "lng": 80.2707,
        "ghi": [5.28, 5.35, 5.42, 5.22, 5.30, 5.38, 5.35, 5.44, 5.40, 5.46],
        "temp": [28.8, 29.1, 29.5, 29.0, 29.3, 29.7, 29.6, 30.0, 29.9, 30.2],
        "sunny_days": 290,
        "dust_index": "Low (Coastal)",
        "panel_temp_loss_pct": 4.6
    },
    "lucknow": {
        "name": "Lucknow",
        "state": "Uttar Pradesh",
        "lat": 26.8467,
        "lng": 80.9462,
        "ghi": [5.18, 5.25, 5.32, 5.12, 5.22, 5.30, 5.28, 5.36, 5.32, 5.38],
        "temp": [25.6, 25.9, 26.3, 25.8, 26.1, 26.5, 26.4, 26.8, 26.7, 27.0],
        "sunny_days": 296,
        "dust_index": "Moderate-High",
        "panel_temp_loss_pct": 4.1
    },
    "patna": {
        "name": "Patna",
        "state": "Bihar",
        "lat": 25.5941,
        "lng": 85.1376,
        "ghi": [5.08, 5.15, 5.22, 5.02, 5.12, 5.20, 5.18, 5.26, 5.22, 5.28],
        "temp": [25.9, 26.2, 26.6, 26.1, 26.4, 26.8, 26.7, 27.1, 27.0, 27.3],
        "sunny_days": 292,
        "dust_index": "Moderate",
        "panel_temp_loss_pct": 4.0
    }
}


# ==============================================================================
# Startup sanity checks: fail fast with a clear message instead of wrong numbers
# ==============================================================================
def _validate_settings() -> None:
    if not 0.1 <= PERFORMANCE_RATIO <= 1.0:
        raise ValueError(f"PERFORMANCE_RATIO must be between 0.1 and 1.0, got {PERFORMANCE_RATIO}")
    if sum(COST_BREAKDOWN.values()) >= 1.0:
        raise ValueError("COST_BREAKDOWN shares must sum to less than 1.0 (the remainder is the net-meter cost)")
    if not -90.0 <= DEFAULT_LAT <= 90.0 or not -180.0 <= DEFAULT_LNG <= 180.0:
        raise ValueError(f"DEFAULT_LAT/DEFAULT_LNG out of range: {DEFAULT_LAT}, {DEFAULT_LNG}")
    if ELECTRICITY_TARIFF_INR_KWH <= 0 or COST_PER_KW_INR <= 0:
        raise ValueError("ELECTRICITY_TARIFF_INR_KWH and COST_PER_KW_INR must be positive")
    for key, factor in ROOF_MATERIAL_USABLE_FACTORS.items():
        if not 0.1 <= factor <= 1.0:
            raise ValueError(f"Usable area factor for '{key}' must be between 0.1 and 1.0, got {factor}")


_validate_settings()