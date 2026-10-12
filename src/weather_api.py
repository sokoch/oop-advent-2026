"""HTTP client helpers for the weather mini-project.

Every network call goes through :func:`robust_get`, which adds:

* a timeout (so a hung server can't freeze the notebook),
* ``raise_for_status()`` (so 4xx/5xx raise instead of silently returning HTML),
* exponential backoff with a retry cap (so transient 5xx recover), and
* a custom :class:`WeatherAPIError` on final failure (so callers catch one type).

Only three public functions: ``geocode``, ``get_forecast``, ``get_metno_forecast``.
"""

from __future__ import annotations

import os
import time
from typing import Any

import requests
from requests.exceptions import RequestException

from src.exceptions import GeocodingError, WeatherAPIError

# --- configuration ------------------------------------------------------------

OPEN_METEO_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
METNO_FORECAST_URL = "https://api.met.no/weatherapi/locationforecast/2.0/compact"

# Fallback string used only when the env var is missing. It is deliberately
# obvious so a developer notices MET Norway's 403 and reads this comment.
PLACEHOLDER_USER_AGENT = (
    "OOP-Advent-2026/1.0 (placeholder@example.com) "
    "-- set USER_AGENT env var for real use"
)


def _user_agent() -> str:
    """Return the User-Agent header to send.

    Reads ``USER_AGENT`` from the environment on **every call**, so exporting
    the variable after import still takes effect.

    MET Norway *requires* an identifying contact (see their terms of service)
    and returns HTTP 403 for placeholder addresses. Open-Meteo has no such
    requirement, but sends whatever we supply.
    """
    return os.environ.get("USER_AGENT") or PLACEHOLDER_USER_AGENT


# --- robustness layer (Part C.1) ---------------------------------------------

def robust_get(
    url: str,
    params: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
    max_retries: int = 3,
    backoff_base: float = 0.5,
) -> requests.Response:
    """GET *url* with retries, exponential backoff, and a hard timeout.

    Parameters
    ----------
    url:
        The endpoint to call.
    params:
        Query string parameters, passed to ``requests`` as ``params=``.
    headers:
        Extra headers. ``User-Agent`` is added automatically if absent.
    timeout:
        Per-attempt timeout in seconds.
    max_retries:
        How many times to retry *after* the first failure. Total attempts =
        ``max_retries + 1``.
    backoff_base:
        Base delay in seconds. Attempt *n* waits ``backoff_base * 2**n``.

    Returns
    -------
    requests.Response
        A response with a 2xx status code, already validated.

    Raises
    ------
    WeatherAPIError
        If all attempts fail (network error, timeout, or 5xx).
    """
    merged_headers = {"User-Agent": _user_agent()}
    if headers:
        merged_headers.update(headers)

    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            response = requests.get(
                url, params=params, headers=merged_headers, timeout=timeout
            )
            # 5xx = server-side problem → retry.
            # 4xx = client-side problem → let raise_for_status handle it (no retry).
            if 500 <= response.status_code < 600:
                raise RequestException(f"Server error {response.status_code}")
            response.raise_for_status()
            return response
        except (RequestException, ConnectionError) as exc:
            last_error = exc
            if attempt == max_retries:
                break
            delay = backoff_base * (2 ** attempt)
            time.sleep(delay)

    raise WeatherAPIError(
        f"GET {url} failed after {max_retries + 1} attempts: {last_error}"
    ) from last_error


# --- geocoding (Part B.1) -----------------------------------------------------

def geocode(name: str, *, count: int = 5) -> list[dict[str, Any]]:
    """Return candidate locations for *name*.

    Parameters
    ----------
    name:
        Free-text place name, e.g. ``"Dresden"`` or ``"Springfield"``.
    count:
        Maximum number of candidates to return.

    Returns
    -------
    list[dict]
        Each entry has ``name``, ``country``, ``admin1``, ``lat``, ``lon``.
        Returns an **empty list** if the API finds nothing — callers must
        handle that case rather than assume ``results`` exists.
    """
    response = robust_get(
        OPEN_METEO_GEOCODE_URL,
        {"name": name, "count": count, "language": "en", "format": "json"},
    )
    payload = response.json()

    # The "results" key is ABSENT when no match is found. Do not use
    # payload["results"] here.
    results = payload.get("results", [])
    return [
        {
            "name": r["name"],
            "country": r.get("country", ""),
            "admin1": r.get("admin1", ""),
            "lat": float(r["latitude"]),
            "lon": float(r["longitude"]),
        }
        for r in results
    ]


def pick_location(
    candidates: list[dict[str, Any]],
    *,
    auto_index: int | None = 0,
) -> dict[str, Any]:
    """Choose one candidate from :func:`geocode`.

    For "Restart & Run All" reproducibility, defaults to the first candidate
    (``auto_index=0``). Pass ``auto_index=None`` to prompt the user instead.

    Raises
    ------
    GeocodingError
        If ``candidates`` is empty.
    """
    if not candidates:
        raise GeocodingError("No locations matched the query.")
    if len(candidates) == 1 or auto_index is not None:
        return candidates[auto_index or 0]
    for i, c in enumerate(candidates):
        print(f"[{i}] {c['name']}, {c['admin1']}, {c['country']} "
              f"({c['lat']}, {c['lon']})")
    return candidates[int(input("Pick index: "))]


# --- Open-Meteo forecast (Part B.2) -------------------------------------------

def get_forecast(lat: float, lon: float, days: int = 7) -> dict[str, Any]:
    """Fetch a daily forecast from Open-Meteo.

    Parameters
    ----------
    lat, lon:
        Coordinates. ``lat`` must be in ``[-90, 90]``, ``lon`` in
        ``[-180, 180]`` — the API rejects invalid ranges with a 400.
    days:
        Number of forecast days (1–16).

    Returns
    -------
    dict
        Raw JSON. The useful parts live under ``payload["daily"]``.
    """
    if not (-90 <= lat <= 90):
        raise ValueError(f"lat out of range: {lat}")
    if not (-180 <= lon <= 180):
        raise ValueError(f"lon out of range: {lon}")

    response = robust_get(
        OPEN_METEO_FORECAST_URL,
        {
            "latitude": lat,
            "longitude": lon,
            "daily": (
                "temperature_2m_max,temperature_2m_min,"
                "precipitation_sum,wind_speed_10m_max,weather_code"
            ),
            "timezone": "auto",       # dates match the location's local day
            "forecast_days": days,
        },
    )
    return response.json()


# --- MET Norway second source (Part C.4) --------------------------------------

def get_metno_forecast(lat: float, lon: float) -> dict[str, Any]:
    """Fetch MET Norway Locationforecast 2.0.

    MET Norway requires an identifying ``User-Agent`` with contact info,
    and asks that clients respect the ``Expires`` header. Our
    :func:`robust_get` already sends the UA.
    """
    response = robust_get(
        METNO_FORECAST_URL,
        {"lat": lat, "lon": lon},
        headers={"User-Agent": _user_agent()},
    )
    return response.json()