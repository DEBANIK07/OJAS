"""
OJAS Elevation Service
Fetches elevation in meters for given latitude and longitude coordinates.
Uses Open-Elevation API with a logged fallback to config.DEFAULT_ELEVATION_M.
"""
import logging
import requests
from typing import Optional

from backend.config import OPEN_ELEVATION_URL, HTTP_TIMEOUT_S, DEFAULT_ELEVATION_M

logger = logging.getLogger("ojas.elevation")


def get_elevation(lat: float, lng: float) -> float:
    """
    Fetch elevation in metres for the given coordinates via Open-Elevation.
    Returns config.DEFAULT_ELEVATION_M (single source of truth) on any failure
    so callers never receive None and never need their own magic number.
    """
    try:
        url = f"{OPEN_ELEVATION_URL}?locations={lat},{lng}"
        response = requests.get(url, timeout=HTTP_TIMEOUT_S)
        response.raise_for_status()
        results = response.json().get("results", [])
        if not results:
            logger.warning("Open-Elevation returned empty results for (%.4f, %.4f); using default %.1f m",
                           lat, lng, DEFAULT_ELEVATION_M)
            return DEFAULT_ELEVATION_M
        elev = results[0].get("elevation")
        if elev is None:
            logger.warning("Open-Elevation result missing 'elevation' key for (%.4f, %.4f); using default %.1f m",
                           lat, lng, DEFAULT_ELEVATION_M)
            return DEFAULT_ELEVATION_M
        return float(elev)
    except Exception as exc:
        logger.warning("Open-Elevation failed for (%.4f, %.4f): %s; using default %.1f m",
                       lat, lng, exc, DEFAULT_ELEVATION_M)
        return DEFAULT_ELEVATION_M
