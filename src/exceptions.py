"""Custom exception hierarchy for both mini-projects."""


class WeatherAPIError(Exception):
    """Raised when a weather API call fails after retries."""


class GeocodingError(WeatherAPIError):
    """Raised when geocoding fails or returns no results."""


class TaxError(Exception):
    """Base class for all tax-domain errors."""


class InvalidIncomeError(TaxError):
    """Raised when income input is negative or otherwise invalid."""


class NoScheduleForDateError(TaxError):
    """Raised when no tax schedule exists for the requested date."""
