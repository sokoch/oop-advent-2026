"""Pure parsing helpers: JSON -> DataFrame, WMO code mapping.

No network calls. Every function takes JSON-shaped input and returns either
a pandas DataFrame or a plain Python structure. This makes the module easy
to test offline with saved sample responses.

Public functions
----------------
* ``forecast_to_dataframe`` — Open-Meteo daily JSON to tidy DataFrame.
* ``metno_to_dataframe`` — MET Norway timeseries JSON to daily DataFrame.
* ``decode_weather_code`` — WMO code int to human-readable string.
* ``summarize_forecast`` — short text summary for the notebook.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

# --- WMO weather interpretation codes ----------------------------------------
# Source: https://open-meteo.com/en/docs (WMO Weather interpretation codes)
WMO_CODES: dict[int, str] = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Drizzle: light",
    53: "Drizzle: moderate",
    55: "Drizzle: dense",
    56: "Freezing drizzle: light",
    57: "Freezing drizzle: dense",
    61: "Rain: slight",
    63: "Rain: moderate",
    65: "Rain: heavy",
    66: "Freezing rain: light",
    67: "Freezing rain: heavy",
    71: "Snow fall: slight",
    73: "Snow fall: moderate",
    75: "Snow fall: heavy",
    77: "Snow grains",
    80: "Rain showers: slight",
    81: "Rain showers: moderate",
    82: "Rain showers: violent",
    85: "Snow showers: slight",
    86: "Snow showers: heavy",
    95: "Thunderstorm: slight or moderate",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}


def decode_weather_code(code: int | float | None) -> str:
    """Map a WMO weather code to a human-readable description.

    Parameters
    ----------
    code:
        Numeric WMO code as returned by Open-Meteo. ``None`` or unknown
        codes return ``"Unknown"``.

    Returns
    -------
    str
        A short English description, e.g. ``"Partly cloudy"``.
    """
    if code is None:
        return "Unknown"
    try:
        key = int(code)
    except (TypeError, ValueError):
        return "Unknown"
    return WMO_CODES.get(key, f"Unknown code {key}")


# --- Open-Meteo parsing -------------------------------------------------------

def forecast_to_dataframe(forecast_json: dict[str, Any]) -> pd.DataFrame:
    """Convert Open-Meteo daily JSON to a tidy DataFrame.

    Parameters
    ----------
    forecast_json:
        The raw dict from ``get_forecast``. Must contain ``"daily"`` with
        parallel arrays keyed by ``time``, ``temperature_2m_max``,
        ``temperature_2m_min``, ``precipitation_sum``,
        ``wind_speed_10m_max``, and ``weather_code``.

    Returns
    -------
    pandas.DataFrame
        Columns: ``date`` (datetime64), ``temp_max_C`` (float),
        ``temp_min_C`` (float), ``precip_mm`` (float), ``wind_max_kmh``
        (float), ``weather_code`` (int), ``description`` (str).
    """
    daily = forecast_json["daily"]

    df = pd.DataFrame(
        {
            "date": pd.to_datetime(daily["time"]),
            "temp_max_C": daily["temperature_2m_max"],
            "temp_min_C": daily["temperature_2m_min"],
            "precip_mm": daily["precipitation_sum"],
            "wind_max_kmh": daily["wind_speed_10m_max"],
            "weather_code": daily["weather_code"],
        }
    )
    df["description"] = df["weather_code"].apply(decode_weather_code)
    return df


# --- MET Norway parsing -------------------------------------------------------

def metno_to_dataframe(metno_json: dict[str, Any]) -> pd.DataFrame:
    """Convert MET Norway Locationforecast 2.0 to a daily DataFrame.

    MET Norway returns hourly (or denser) timeseries entries. We pick the
    ``next_1_hours`` precipitation when present, and aggregate to daily
    min/max temperature and total precipitation so the output is directly
    comparable to Open-Meteo.

    Parameters
    ----------
    metno_json:
        The raw dict from ``get_metno_forecast``.

    Returns
    -------
    pandas.DataFrame
        Columns: ``date`` (datetime64), ``temp_max_C``, ``temp_min_C``,
        ``precip_mm``.
    """
    rows: list[dict[str, Any]] = []
    for entry in metno_json["properties"]["timeseries"]:
        time = pd.Timestamp(entry["time"])
        details = entry["data"]["instant"]["details"]
        next_1h = entry["data"].get("next_1_hours", {})
        precip = next_1h.get("details", {}).get("precipitation_amount", 0.0)
        rows.append(
            {
                "time": time,
                "temp_C": details.get("air_temperature"),
                "precip_mm": precip,
            }
        )

    df = pd.DataFrame(rows).set_index("time")
    daily = df.resample("D").agg(
        {"temp_C": ["min", "max"], "precip_mm": "sum"}
    )
    daily.columns = ["temp_min_C", "temp_max_C", "precip_mm"]
    daily = daily.reset_index().rename(columns={"time": "date"})
    # Normalize to tz-naive calendar dates for comparability with
    # Open-Meteo's daily output (which is also tz-naive).
    daily["date"] = (
        pd.to_datetime(daily["date"]).dt.tz_localize(None).dt.normalize()
    )
    return daily


# --- text summary -------------------------------------------------------------

def summarize_forecast(df: pd.DataFrame, location: str) -> str:
    """Return a one-paragraph plain-English summary of the forecast.

    Parameters
    ----------
    df:
        Output of :func:`forecast_to_dataframe`.
    location:
        Display name of the location, e.g. ``"Dresden, Germany"``.

    Returns
    -------
    str
        A short summary suitable for printing above the table.
    """
    if df.empty:
        return f"No forecast data available for {location}."

    t_min = df["temp_min_C"].min()
    t_max = df["temp_max_C"].max()
    total_precip = df["precip_mm"].sum()
    wettest_day = df.loc[df["precip_mm"].idxmax()]
    wind_max = df["wind_max_kmh"].max()

    return (
        f"{location}: {len(df)}-day forecast.\n"
        f"  Temperature range: {t_min:.1f} °C to {t_max:.1f} °C.\n"
        f"  Total precipitation: {total_precip:.1f} mm "
        f"(wettest day: {wettest_day['date'].date()} "
        f"with {wettest_day['precip_mm']:.1f} mm).\n"
        f"  Maximum wind speed: {wind_max:.1f} km/h.\n"
        f"  Data source: Open-Meteo (CC-BY 4.0)."
    )