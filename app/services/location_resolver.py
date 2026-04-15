import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import requests

logger = logging.getLogger(__name__)

GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")

_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
_TIMEZONE_URL = "https://maps.googleapis.com/maps/api/timezone/json"

STATUS_RESOLVED = "resolved"
STATUS_NOT_FOUND = "not_found"
STATUS_AMBIGUOUS = "ambiguous"
STATUS_API_ERROR = "api_error"


@dataclass
class LocationResolution:
    status: str
    raw_input: str
    normalized_name: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    timezone: Optional[str] = None
    reason: Optional[str] = None


def _component(components: list, target_type: str) -> Optional[str]:
    for comp in components:
        if target_type in comp.get("types", []):
            return comp.get("long_name")
    return None


def _build_normalized_name(components: list) -> Optional[str]:
    city = (
        _component(components, "locality")
        or _component(components, "postal_town")
        or _component(components, "administrative_area_level_2")
        or _component(components, "administrative_area_level_1")
    )
    region = _component(components, "administrative_area_level_1")
    country = _component(components, "country")

    parts = [p for p in (city, region if region != city else None, country) if p]
    return ", ".join(parts) if parts else None


def _countries_differ(results: list) -> bool:
    countries = set()
    for result in results[:3]:
        country = _component(result.get("address_components", []), "country")
        if country:
            countries.add(country)
    return len(countries) > 1


def _fetch_timezone(lat: float, lng: float) -> Optional[str]:
    try:
        response = requests.get(
            _TIMEZONE_URL,
            params={
                "location": f"{lat},{lng}",
                "timestamp": int(datetime.utcnow().timestamp()),
                "key": GOOGLE_MAPS_API_KEY,
            },
            timeout=5,
        )
        data = response.json()
        if data.get("status") == "OK":
            return data.get("timeZoneId")
        logger.warning("Timezone API non-OK status: %s", data.get("status"))
        return None
    except Exception as exc:
        logger.warning("Timezone API error: %s", exc)
        return None


def resolve_location(raw_city: str) -> LocationResolution:
    """
    Resolve a user-typed city string via Google Maps Geocoding + Timezone APIs.

    Never raises — returns a LocationResolution with a status field so callers
    can branch deterministically. Onboarding must not block on API failure.
    """
    raw = (raw_city or "").strip()
    if not raw:
        return LocationResolution(status=STATUS_NOT_FOUND, raw_input=raw, reason="empty input")

    if not GOOGLE_MAPS_API_KEY:
        logger.warning("GOOGLE_MAPS_API_KEY not set — location_resolver returning api_error")
        return LocationResolution(status=STATUS_API_ERROR, raw_input=raw, reason="api_key_missing")

    try:
        response = requests.get(
            _GEOCODE_URL,
            params={"address": raw, "key": GOOGLE_MAPS_API_KEY},
            timeout=5,
        )
        data = response.json()
    except Exception as exc:
        logger.warning("Geocoding API error for %r: %s", raw, exc)
        return LocationResolution(status=STATUS_API_ERROR, raw_input=raw, reason=str(exc))

    status = data.get("status")

    if status == "ZERO_RESULTS":
        return LocationResolution(status=STATUS_NOT_FOUND, raw_input=raw, reason="zero_results")

    if status != "OK":
        logger.warning("Geocoding non-OK status for %r: %s", raw, status)
        return LocationResolution(status=STATUS_API_ERROR, raw_input=raw, reason=status or "unknown")

    results = data.get("results", [])
    if not results:
        return LocationResolution(status=STATUS_NOT_FOUND, raw_input=raw, reason="empty_results")

    if len(results) > 1 and _countries_differ(results):
        return LocationResolution(status=STATUS_AMBIGUOUS, raw_input=raw, reason="multiple_countries")

    best = results[0]
    components = best.get("address_components", [])
    location = best.get("geometry", {}).get("location", {})
    lat = location.get("lat")
    lng = location.get("lng")

    if lat is None or lng is None:
        return LocationResolution(status=STATUS_API_ERROR, raw_input=raw, reason="no_coordinates")

    normalized = _build_normalized_name(components) or best.get("formatted_address")
    timezone = _fetch_timezone(lat, lng)

    if timezone is None:
        return LocationResolution(
            status=STATUS_API_ERROR,
            raw_input=raw,
            normalized_name=normalized,
            latitude=lat,
            longitude=lng,
            reason="timezone_lookup_failed",
        )

    return LocationResolution(
        status=STATUS_RESOLVED,
        raw_input=raw,
        normalized_name=normalized,
        latitude=lat,
        longitude=lng,
        timezone=timezone,
    )
