"""Offline tests for weather parsing helpers.

All tests use saved JSON fixtures under ``tests/data/`` so they run without
network access. This is required by the assignment: "Use saved sample JSON
so they run offline."

Fixtures:
* ``geocode_dresden.json`` — real Open-Meteo geocoding response for Dresden.
* ``geocode_missing.json`` — real Open-Meteo response for an unknown name
  (the ``results`` key is absent in this case).
* ``forecast_dresden.json`` — real Open-Meteo daily forecast response.
"""

from __future__ import annotations

import json
import pathlib

import pandas as pd
import pytest

from src.weather_parsing import (
    decode_weather_code,
    forecast_to_dataframe,
    summarize_forecast,
)

# --- fixtures -----------------------------------------------------------------

DATA_DIR = pathlib.Path(__file__).parent / "data"


def _load(name: str) -> dict:
    """Load one JSON fixture by filename."""
    return json.loads((DATA_DIR / name).read_text())


@pytest.fixture
def geocode_dresden() -> dict:
    return _load("geocode_dresden.json")


@pytest.fixture
def geocode_missing() -> dict:
    return _load("geocode_missing.json")


@pytest.fixture
def forecast_dresden() -> dict:
    return _load("forecast_dresden.json")


# --- geocode fixture shape (asserts the API contract we rely on) --------------

def test_geocode_dresden_has_expected_fields(geocode_dresden: dict) -> None:
    """The geocoding response must have a 'results' list with lat/lon."""
    assert "results" in geocode_dresden
    results = geocode_dresden["results"]
    assert isinstance(results, list)
    assert len(results) >= 1

    first = results[0]
    # Our geocode() function relies on these exact keys.
    for key in ("name", "latitude", "longitude"):
        assert key in first, f"missing key {key!r} in geocode result"


def test_geocode_missing_has_no_results_key(geocode_missing: dict) -> None:
    """When no match is found, the 'results' key is absent.

    Our geocode() uses payload.get("results", []) precisely because of this.
    This test locks in that assumption so we notice if the API ever changes.
    """
    assert "results" not in geocode_missing, (
        "API contract changed: 'results' key appeared for a missing query"
    )


# --- DataFrame shape ----------------------------------------------------------

def test_forecast_dataframe_columns(forecast_dresden: dict) -> None:
    """The DataFrame exposes exactly the columns our notebook relies on."""
    df = forecast_to_dataframe(forecast_dresden)
    expected_columns = {
        "date",
        "temp_max_C",
        "temp_min_C",
        "precip_mm",
        "wind_max_kmh",
        "weather_code",
        "description",
    }
    assert expected_columns.issubset(set(df.columns))
    assert len(df) == len(forecast_dresden["daily"]["time"])
    assert pd.api.types.is_datetime64_any_dtype(df["date"])


def test_forecast_temperature_ordering(forecast_dresden: dict) -> None:
    """Maximum temperature must be >= minimum temperature for each day."""
    df = forecast_to_dataframe(forecast_dresden)
    assert (df["temp_max_C"] >= df["temp_min_C"]).all()


def test_forecast_precipitation_non_negative(forecast_dresden: dict) -> None:
    """Precipitation sums can't be negative."""
    df = forecast_to_dataframe(forecast_dresden)
    assert (df["precip_mm"] >= 0).all()


# --- WMO code decoding --------------------------------------------------------

@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (0, "Clear sky"),
        (2, "Partly cloudy"),
        (61, "Rain: slight"),
        (95, "Thunderstorm: slight or moderate"),
    ],
)
def test_decode_weather_code_known_values(code: int, expected: str) -> None:
    """Known WMO codes decode to their canonical descriptions."""
    assert decode_weather_code(code) == expected


def test_decode_weather_code_unknown_and_none() -> None:
    """Unknown codes and None degrade gracefully to a string, never crash."""
    assert decode_weather_code(None) == "Unknown"
    assert decode_weather_code(9999).startswith("Unknown")
    assert decode_weather_code("not-a-code") == "Unknown"


def test_every_wmo_code_in_forecast_is_decodable(forecast_dresden: dict) -> None:
    """Every weather_code in the fixture maps to a non-'Unknown' description."""
    codes = forecast_dresden["daily"]["weather_code"]
    for c in codes:
        description = decode_weather_code(c)
        assert not description.startswith("Unknown"), (
            f"unmapped code {c!r} (add it to WMO_CODES)"
        )


# --- text summary -------------------------------------------------------------

def test_summarize_forecast_mentions_key_facts(forecast_dresden: dict) -> None:
    """Summary mentions the location, units, and data source."""
    df = forecast_to_dataframe(forecast_dresden)
    summary = summarize_forecast(df, "Dresden, Germany")

    assert "Dresden, Germany" in summary
    assert "°C" in summary
    assert "mm" in summary
    assert "Open-Meteo" in summary  # data-source attribution


def test_summarize_forecast_empty_dataframe() -> None:
    """An empty DataFrame returns a short 'no data' message, not an exception."""
    empty = pd.DataFrame(
        columns=[
            "date",
            "temp_max_C",
            "temp_min_C",
            "precip_mm",
            "wind_max_kmh",
            "weather_code",
            "description",
        ]
    )
    summary = summarize_forecast(empty, "Nowhere")
    assert "No forecast data" in summary
    assert "Nowhere" in summary