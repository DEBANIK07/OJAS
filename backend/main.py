"""
OJAS Backend API Server (FastAPI)
Provides RESTful APIs for the Solar Calculation Engine, Geocoding, & Environmental Data.
"""

import json
import os
import uuid
import random
import requests as _requests
from pathlib import Path
from typing import Optional, Dict, Any, List

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend.config import (
    ALMM_PANEL_CATALOG,
    DEFAULT_USABLE_AREA_FACTOR,
    ROOF_MATERIAL_USABLE_FACTORS,
    DISTRICT_CLIMATE_DATA,
    HISTORICAL_YEARS,
    DEFAULT_ELEVATION_M,
    DEFAULT_LAT,
    DEFAULT_LNG,
    GEOAPIFY_API_KEY,
    CORS_ORIGINS,
    APP_ENV,
)
from backend.services.solar_engine import calculate_energy_estimate
from backend.services.elevation_service import get_elevation
from backend.services.weather_service import (
    fetch_weather_history,
    process_weather_data,
    five_year_average,
    ten_year_yearly_breakdown,
    derive_weather_views,
    get_weather_history_data,
)
from backend.database import ensure_schema, save_quote

# ---------------------------------------------------------------------------
# App & middleware
# ---------------------------------------------------------------------------

app = FastAPI(
    title="OJAS Solar Energy Calculation Engine",
    description="PM Surya Ghar Rooftop Solar Assessment Platform API",
    version="1.0.0",
)

# CORS: use config list (populated from CORS_ORIGINS env var); fall back to * in
# development when no explicit list is configured.
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS or ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Startup: load vendors from file; initialise DB schema
# ---------------------------------------------------------------------------

_VENDORS_PATH = Path(__file__).resolve().parent / "data" / "vendors.json"
_VENDORS: List[Dict[str, Any]] = json.loads(_VENDORS_PATH.read_text(encoding="utf-8"))

# No-op when DATABASE_URL is not set; logs a warning instead of crashing.
ensure_schema()

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class GeometryPayload(BaseModel):
    centroidLat: Optional[float] = DEFAULT_LAT
    centroidLng: Optional[float] = DEFAULT_LNG
    roofTilt: Optional[float] = 15.0
    solarAzimuth: Optional[float] = 180.0


class EnergyEstimateRequest(BaseModel):
    polygon_area_m2: float = Field(..., description="Rooftop polygon area in square meters", example=100.0)
    latitude: Optional[float] = Field(default=DEFAULT_LAT, description="Centroid latitude")
    longitude: Optional[float] = Field(default=DEFAULT_LNG, description="Centroid longitude")
    elevation_m: Optional[float] = Field(default=DEFAULT_ELEVATION_M, description="Terrain elevation in meters")
    roof_tilt: Optional[float] = Field(default=15.0, description="Racking tilt angle in degrees")
    solar_azimuth: Optional[float] = Field(default=180.0, description="Solar facing azimuth angle in degrees")
    bill_offset_percent: Optional[float] = Field(default=100.0, ge=10.0, le=100.0, description="User slider bill offset %")
    panel_type: Optional[str] = Field(default="monocrystalline", description="Selected ALMM panel type")
    roof_material: Optional[str] = Field(default="rcc", description="Roof construction material ('rcc', 'tin', 'tile', 'asbestos', 'wood')")
    usable_area_factor: Optional[float] = Field(default=None, description="Optional custom usable area factor override (0.1 to 1.0)")
    monthly_units: Optional[float] = Field(default=None, description="Monthly electricity consumption (kWh). Used to cap system size to actual demand.")
    geometry: Optional[GeometryPayload] = Field(default=None, description="Optional nested Phase 2 geometry object")


class VendorQuoteRequest(BaseModel):
    vendor_name: str
    customer_name: str
    phone: str
    latitude: Optional[float] = DEFAULT_LAT
    longitude: Optional[float] = DEFAULT_LNG
    system_capacity_kw: Optional[float] = 3.8
    notes: Optional[str] = None

# ---------------------------------------------------------------------------
# Health / catalog
# ---------------------------------------------------------------------------

@app.get("/api/v1/health", summary="Health check endpoint")
async def health_check():
    return {
        "status": "ONLINE",
        "platform": "OJAS Surya Ghar Solar Assessment Platform",
        "version": "1.0.0",
    }


@app.get("/api/v1/panel-catalog", summary="Get ALMM reference panel catalog")
async def get_panel_catalog():
    return {
        "catalog": ALMM_PANEL_CATALOG,
        "usable_area_factor": DEFAULT_USABLE_AREA_FACTOR,
        "material_usable_factors": ROOF_MATERIAL_USABLE_FACTORS,
    }

# ---------------------------------------------------------------------------
# Energy estimate
# ---------------------------------------------------------------------------

@app.post("/api/energy-estimate", summary="Compute energy estimate")
@app.post("/api/v1/energy-estimate", summary="Compute energy estimate (v1)")
async def estimate_energy(req: EnergyEstimateRequest):
    try:
        lat = req.latitude
        lng = req.longitude
        tilt = req.roof_tilt
        azimuth = req.solar_azimuth

        # Extract nested geometry if provided from Phase 2 payload
        if req.geometry:
            if req.geometry.centroidLat is not None:
                lat = req.geometry.centroidLat
            if req.geometry.centroidLng is not None:
                lng = req.geometry.centroidLng
            if req.geometry.roofTilt is not None:
                tilt = req.geometry.roofTilt
            if req.geometry.solarAzimuth is not None:
                azimuth = req.geometry.solarAzimuth

        result = calculate_energy_estimate(
            polygon_area_m2=req.polygon_area_m2,
            latitude=lat if lat is not None else DEFAULT_LAT,
            longitude=lng if lng is not None else DEFAULT_LNG,
            elevation_m=req.elevation_m or DEFAULT_ELEVATION_M,
            roof_tilt=tilt if tilt is not None else 15.0,
            solar_azimuth=azimuth if azimuth is not None else 180.0,
            bill_offset_percent=req.bill_offset_percent or 100.0,
            panel_type=req.panel_type or "monocrystalline",
            roof_material=req.roof_material or "rcc",
            usable_area_factor=req.usable_area_factor,
            monthly_units=req.monthly_units,
        )
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Energy calculation error: {str(exc)}")

# ---------------------------------------------------------------------------
# Solar potential quick-read
# ---------------------------------------------------------------------------

@app.get("/api/v1/solar-potential", summary="Quick solar potential & subsidy readout")
async def get_solar_potential(
    lat: float = DEFAULT_LAT,
    lng: float = DEFAULT_LNG,
    mode: str = "building",
    area_sqft: Optional[float] = None,
    material: str = "rcc",
):
    """Calculates instantaneous solar feasibility, capacity, subsidy, and savings for coordinates."""
    sqft = area_sqft if area_sqft and area_sqft > 50 else 650.0
    area_m2 = sqft * 0.092903
    estimate = calculate_energy_estimate(
        polygon_area_m2=area_m2, latitude=lat, longitude=lng,
        roof_tilt=15.0, solar_azimuth=180.0,
        panel_type="monocrystalline", roof_material=material,
    )
    fin = estimate["financial_environmental_impact"]
    return {
        "status": "SUCCESS",
        "latitude": lat, "longitude": lng, "mode": mode, "area_sqft": sqft,
        **{k: estimate["area_derivation"][k]
           for k in ("gross_area_m2", "usable_area_m2", "usable_area_sqft", "usable_area_percent")},
        "estimated_capacity_kw": estimate["system_sizing"]["system_capacity_kw"],
        "annual_generation_kwh": estimate["pvlib_simulation"]["annual_generation_kwh"],
        "subsidy_amount_inr": fin["pm_surya_ghar_subsidy_inr"],
        "payback_years": fin["payback_period_years"],
        "co2_offset_tons": fin["co2_offset_tons_yr"],
    }

# ---------------------------------------------------------------------------
# Weather history
# ---------------------------------------------------------------------------

def find_closest_district(lat: float, lng: float, district_hint: Optional[str] = None):
    """Matches coordinates or a name hint to the closest representative district dataset."""
    if district_hint:
        hint = district_hint.lower().replace("metro", "").strip()
        for key, data in DISTRICT_CLIMATE_DATA.items():
            if key in hint or data["name"].lower() in hint or hint in data["name"].lower():
                return data
    best_dist, best_match = float("inf"), DISTRICT_CLIMATE_DATA["kolkata"]
    for data in DISTRICT_CLIMATE_DATA.values():
        d = ((lat - data["lat"]) ** 2 + (lng - data["lng"]) ** 2) ** 0.5
        if d < best_dist:
            best_dist, best_match = d, data
    return best_match


@app.get("/api/weather-history", summary="10-Year historical weather data")
@app.get("/api/v1/weather-history", summary="10-Year historical weather data (v1)")
async def get_weather_history(
    lat: float = DEFAULT_LAT,
    lon: Optional[float] = None,
    lng: Optional[float] = None,
    district: Optional[str] = None,
):
    """
    Fetches 10 years of historical solar/weather data from Open-Meteo Archive API.
    Falls back to the district dataset when Open-Meteo is unreachable, returning the
    SAME top-level keys as the success path so callers need no special-casing.
    """
    effective_lon = lon if lon is not None else (lng if lng is not None else DEFAULT_LNG)
    try:
        return get_weather_history_data(lat, effective_lon)
    except Exception:
        d = find_closest_district(lat, effective_lon, district)
        dist_offset = ((lat - d["lat"]) ** 2 + (effective_lon - d["lng"]) ** 2) ** 0.5
        lat_factor = 1.0 + (d["lat"] - lat) * 0.008 if dist_offset > 0.5 else 1.0

        ghi_series  = [round(v * lat_factor, 2) for v in d["ghi"]]
        temp_series = [round(v, 1) for v in d["temp"]]
        years_int   = [int(y) for y in HISTORICAL_YEARS]
        end_yr      = years_int[-1]

        five_yr_avg_ghi  = round(sum(ghi_series[-5:]) / 5, 2)
        five_yr_avg_temp = round(sum(temp_series[-5:]) / 5, 1)

        # Mirrors weather_service.five_year_average() output shape
        calculation_input = {
            "avg_ghi_kwh_m2_day": five_yr_avg_ghi,
            "avg_temp_c": five_yr_avg_temp,
            "avg_wind_ms": 2.5,
            "years_used": f"{end_yr - 4}-{end_yr}",
        }
        # Mirrors weather_service.ten_year_yearly_breakdown() output shape
        yearly_variation = [
            {"year": yr, "avg_ghi_kwh_m2_day": ghi_series[i]}
            for i, yr in enumerate(years_int)
        ]

        return {
            "status": "SUCCESS",
            "source": "district-fallback",
            "district": d["name"],
            "state": d["state"],
            "latitude": lat,
            "longitude": effective_lon,
            "data_source": "District Dataset Fallback",
            # Keys shared with live success path ↓
            "calculation_input": calculation_input,
            "yearly_variation": yearly_variation,
            "five_year_average": calculation_input,
            "ten_year_breakdown": yearly_variation,
            "years": HISTORICAL_YEARS,
            "solar_radiation_ghi": ghi_series,
            "avg_annual_ghi": round(sum(ghi_series) / len(ghi_series), 2),
            "mean_temp_c": round(sum(temp_series) / len(temp_series), 1),
            # Extra context (ignored by callers that only use the shared keys above)
            "avg_temperature_c": temp_series,
            "avg_annual_sunny_days": d["sunny_days"],
            "dust_index": d["dust_index"],
            "panel_temp_loss_pct": d["panel_temp_loss_pct"],
        }

# ---------------------------------------------------------------------------
# Environmental data (Phase 2)
# ---------------------------------------------------------------------------

@app.get("/api/v1/environmental-data", summary="Rooftop Environmental & Meteorological Data Pipeline")
def get_environmental_data(
    lat: float = Query(...),
    lng: float = Query(...),
    bearing: float = Query(...),
):
    """Returns 10-year meteorological, elevation, and roof orientation data for Phase 2."""
    try:
        raw = fetch_weather_history(lat, lng)
        df = process_weather_data(raw)
        calc_input = five_year_average(df)
        yearly = ten_year_yearly_breakdown(df)
        elevation_m = get_elevation(lat, lng)   # always a float; logs on failure

        ghi      = calc_input.get("avg_ghi_kwh_m2_day", 5.18)
        avg_temp = calc_input.get("avg_temp_c", 26.5)
        avg_wind = calc_input.get("avg_wind_ms", 3.2)

        return {
            "location": {"lat": lat, "lng": lng},
            "roof_bearing_deg": bearing,
            "elevation_m": elevation_m,
            "calculation_input": calc_input,
            "yearly_variation": yearly,
            "data_source": "open-meteo",
            "solar": {
                "ghi_kwh_m2_day": ghi,
                "dni_kwh_m2_day": round(ghi * 1.15, 2),
                "dhi_kwh_m2_day": round(ghi * 0.38, 2),
                "psh_hours_day":  round(ghi * 0.96, 2),
                "annual_irradiance_kwh_m2": round(ghi * 365.25),
            },
            "weather": {
                "temp_avg_c": avg_temp,
                "temp_max_c": round(avg_temp + 12, 1),
                "wind_speed_ms": avg_wind,
                "humidity_percent": 60,
                "clear_sky_days": 290,
            },
            "terrain": {
                "elevation_m": elevation_m,
                "optimal_racking_tilt": round(abs(lat), 1),
                "albedo": 0.20,
                "surface_type": "Concrete / RCC Terrace",
            },
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Environmental data fetch failed: {str(e)}")

# ---------------------------------------------------------------------------
# District wards  (illustrative — no real ward-level solar dataset available)
# ---------------------------------------------------------------------------

@app.get("/api/v1/district-wards", summary="Municipal district solar potential & feeder heatmap (illustrative)")
async def get_district_wards(district: str = "Kolkata Metro"):
    """
    Returns 36-ward solar potential scores, estimated capacity, and substation headroom.

    **data_note: "illustrative"** — scores, MWp figures, and headroom percentages are
    seeded pseudo-random values used for UI demonstration. No real ward-level solar
    dataset was available. Do not present these figures as measurements.
    """
    d_clean = district.lower()
    if "delhi" in d_clean:
        areas = ["Connaught Place","Chanakyapuri","Karol Bagh","Dwarka Sec 6","Dwarka Sec 12","Rohini Sec 3","Rohini Sec 10","Vasant Kunj","Saket","Hauz Khas","Lajpat Nagar","Defense Colony","Greater Kailash","Nehru Place","Mayur Vihar 1","Mayur Vihar 2","Janakpuri","Rajouri Garden","Pitampura","Model Town","Civil Lines","Chandni Chowk","Paharganj","Sarita Vihar","Jasola","Okhla Phase 3","Patparganj","Preet Vihar","Shahdara","Kashmere Gate","Paschim Vihar","Punjabi Bagh","Vikaspuri","Uttam Nagar","Narela","Najafgarh"]
    elif "jaipur" in d_clean:
        areas = ["C-Scheme","Malviya Nagar","Mansarovar North","Mansarovar South","Vaishali Nagar","Raja Park","Bapu Nagar","Civil Lines","Tonk Road","Jagatpura","Sitapura","Sanganer","Ajmer Road","Vidhyadhar Nagar","Shastri Nagar","Bani Park","Jhotwara","Murlipura","Amer","Hawa Mahal","Johari Bazar","MI Road","Sodala","Gopalpura","Pratap Nagar","Durgapura","Barkat Nagar","Lal Kothi","Adarsh Nagar","Tilak Nagar","Sirsi Road","Kalwar Road","Agra Road","Delhi Road","Kukas","Chomu"]
    elif "bengaluru" in d_clean or "bangalore" in d_clean:
        areas = ["Indiranagar","Koramangala","HSR Layout","Whitefield","Electronic City","Jayanagar","JP Nagar","BTM Layout","Marathahalli","Hebbal","Malleshwaram","Rajajinagar","Basavanagudi","Frazer Town","Sadashivanagar","Yelahanka","Banashankari","Bellandur","Sarjapur Road","Varthur","Bannerghatta","Kalyan Nagar","Kammanahalli","RT Nagar","Ulsoor","MG Road","Cunningham Road","Richmond Town","Domlur","Kaggadasapura","Vidyaranyapura","Sahakara Nagar","Peenya","Yeshwanthpur","Vijayanagar","Nagarbhavi"]
    elif "mumbai" in d_clean:
        areas = ["Colaba","Marine Lines","Fort","Malabar Hill","Nariman Point","Worli","Lower Parel","Dadar North","Dadar South","Bandra West","Bandra East","Khar","Santacruz","Vile Parle","Andheri West","Andheri East","Juhu","Goregaon West","Goregaon East","Malad","Kandivali","Borivali","Dahisar","Powai","Ghatkopar","Vikhroli","Bhandup","Mulund","Kurla","Chembur","Sion","Matunga","Wadala","Byculla","Parel","Mahim"]
    elif "nagpur" in d_clean:
        areas = ["Civil Lines","Dharampeth","Ramdaspeth","Sitabuldi","Dhantoli","Congress Nagar","Pratap Nagar","Laxmi Nagar","Bajaj Nagar","Trimurti Nagar","Khamla","Wardha Road","Manewada","Ayodhya Nagar","Nandanvan","Sakkardara","Mahal","Gandhibagh","Itwari","Hansapuri","Jaripatka","Kadbi Chowk","Sadrar","Katol Road","Gorewada","Zingabai Takli","Mankapur","Friend's Colony","Seminary Hills","Ravi Nagar","Wadi","MIDC Hingna","Butibori","MIHAN","Pardi","Kalamna"]
    else:
        areas = ["Shyambazar","Bagbazar","Cossipore","Maniktala","Kankurgachi","Ultadanga","Salt Lake Sec 1","Salt Lake Sec 2","New Town North","Rajarhat","Sealdah","College Street","Gariahat","Park Street","Bhowanipore","Alipore","Ballygunge","Dhakuria","Jadavpur","Tollygunge","Behala West","Behala East","Garia","Narendrapur","Sonarpur","Barasat North","Barasat South","Madhyamgram","Sodepur","Barrackpore","Howrah Station","Shibpur","Bally","Dankuni","Serampore","Chandannagar"]

    rnd = random.Random(sum(ord(c) for c in district))
    wards = [
        {
            "name": f"Ward {i + 1} ({area})",
            "score": round(rnd.uniform(7.4, 9.8), 1),
            "potential": f"{round(rnd.uniform(2.8, 11.5), 1)} MWp",
            "headroom": f"{rnd.randint(45, 96)}%",
        }
        for i, area in enumerate(areas)
    ]
    return {"district": district, "total_wards": len(wards), "data_note": "illustrative", "wards": wards}

# ---------------------------------------------------------------------------
# Vendors (loaded from file at startup)
# ---------------------------------------------------------------------------

@app.get("/api/v1/vendors", summary="Get empanelled MNRE solar EPC vendors")
async def get_empanelled_vendors():
    """Returns directory of accredited solar installers loaded from backend/data/vendors.json."""
    return {"status": "SUCCESS", "vendors": _VENDORS}

# ---------------------------------------------------------------------------
# Vendor quote  (persisted to Postgres when DATABASE_URL is set)
# ---------------------------------------------------------------------------

@app.post("/api/v1/vendor-quote", summary="Submit application dossier to empanelled vendor")
async def submit_vendor_quote(req: VendorQuoteRequest):
    """
    Generates a collision-resistant tracking ID, persists the quote request to Postgres
    (when DATABASE_URL is set), and returns the ID immediately so the user is never blocked
    by DB availability.
    """
    if not req.customer_name or not req.phone:
        raise HTTPException(status_code=400, detail="Customer name and phone number are required.")

    tracking_id = "OJAS-SURYA-" + uuid.uuid4().hex[:8].upper()
    save_quote(
        tracking_id=tracking_id,
        vendor_name=req.vendor_name,
        customer_name=req.customer_name,
        phone=req.phone,
        latitude=req.latitude,
        longitude=req.longitude,
        system_capacity_kw=req.system_capacity_kw,
        notes=req.notes,
    )
    return {
        "status": "SUBMITTED",
        "tracking_id": tracking_id,
        "vendor": req.vendor_name,
        "customer": req.customer_name,
        "phone": req.phone,
        "message": f"Dossier successfully routed to {req.vendor_name}. An engineer will contact within 24 hours.",
    }

# ---------------------------------------------------------------------------
# Geocoding proxy  (keeps GEOAPIFY_API_KEY out of the browser)
# ---------------------------------------------------------------------------

@app.get("/api/v1/reverse-geocode", summary="Reverse-geocode proxy (keeps Geoapify key server-side)")
def reverse_geocode(lat: float, lng: float):
    """Proxies Geoapify reverse-geocoding. Frontend parsing stays identical."""
    if not GEOAPIFY_API_KEY:
        raise HTTPException(status_code=503, detail="Geocoder not configured — set GEOAPIFY_API_KEY")
    r = _requests.get(
        "https://api.geoapify.com/v1/geocode/reverse",
        params={"lat": lat, "lon": lng, "apiKey": GEOAPIFY_API_KEY},
        timeout=8,
    )
    r.raise_for_status()
    return r.json()

# ---------------------------------------------------------------------------
# Static assets & frontend routes
# ---------------------------------------------------------------------------

ROOT_DIR = Path(__file__).resolve().parent.parent

for _mount, _sub in [("css", "css"), ("js", "js"), ("images", "images")]:
    if (ROOT_DIR / _sub).is_dir():
        app.mount(f"/{_mount}", StaticFiles(directory=str(ROOT_DIR / _sub)), name=_mount)


@app.get("/", summary="Serve OJAS Main Portal")
async def serve_index():
    p = ROOT_DIR / "index.html"
    return FileResponse(str(p)) if p.exists() else {"status": "ONLINE", "message": "OJAS API is active"}


@app.get("/assessment.html", summary="Serve OJAS Assessment Portal")
async def serve_assessment():
    p = ROOT_DIR / "assessment.html"
    return FileResponse(str(p)) if p.exists() else FileResponse(str(ROOT_DIR / "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000)),
        reload=(APP_ENV == "development"),
    )
