"""Money value object for UGX amounts.

Design notes
------------
* ``Decimal`` only. Never ``float`` — floating-point rounding errors
  compound in tax calculations and produce incorrect filings.
* Frozen dataclass. Instances are immutable and hashable.
* Default rounding is ROUND_HALF_UP to whole shillings, matching URA's
  published practice for assessments.
* Currency is a first-class part of the value: adding UGX to USD raises.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Union

NumberLike = Union[int, str, Decimal, "Money"]


@dataclass(frozen=True)
class Money:
    """An immutable monetary amount.

    Examples
    --------
    >>> Money(Decimal("1000")) + Money(Decimal("500"))
    Money(1500 UGX)
    >>> Money(Decimal("335000")) < Money(Decimal("410000"))
    True
    >>> Money(Decimal("335000.4"))  # rounds HALF_UP to whole shillings
    Money(335000 UGX)
    >>> Money(Decimal("335000.5"))
    Money(335001 UGX)
    """

    amount: Decimal
    currency: str = "UGX"

    # ----- construction ------------------------------------------------------

    def __post_init__(self) -> None:
        # Coerce to Decimal so callers can't accidentally pass a float.
        if not isinstance(self.amount, Decimal):
            object.__setattr__(self, "amount", Decimal(str(self.amount)))
        # Quantize to whole shillings with HALF_UP.
        quantized = self.amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        object.__setattr__(self, "amount", quantized)
        if not isinstance(self.currency, str) or not self.currency:
            raise ValueError("currency must be a non-empty string")

    @classmethod
    def of(cls, value: NumberLike, currency: str = "UGX") -> "Money":
        """Convenience constructor accepting int, str, Decimal, or Money."""
        if isinstance(value, Money):
            if value.currency != currency:
                raise ValueError(
                    f"currency mismatch: {value.currency} != {currency}"
                )
            return value
        if isinstance(value, float):
            raise TypeError(
                "Money.of() refuses floats — pass Decimal, int, or str instead"
            )
        return cls(Decimal(value), currency)

    # ----- arithmetic --------------------------------------------------------

    def __add__(self, other: "Money") -> "Money":
        self._require_same_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: "Money") -> "Money":
        self._require_same_currency(other)
        return Money(self.amount - other.amount, self.currency)

    def __mul__(self, factor: NumberLike) -> "Money":
        """Multiply by a scalar (int, Decimal, or str). Float is rejected."""
        if isinstance(factor, Money):
            raise TypeError("Money * Money is meaningless; use a scalar factor")
        if isinstance(factor, float):
            raise TypeError(
                "Money * float is unsafe — use Decimal or str for the factor"
            )
        if isinstance(factor, str):
            factor = Decimal(factor)
        elif isinstance(factor, int):
            factor = Decimal(factor)
        return Money(self.amount * factor, self.currency)

    __rmul__ = __mul__  # allow scalar * Money

    # ----- comparison --------------------------------------------------------

    def __lt__(self, other: "Money") -> bool:
        self._require_same_currency(other)
        return self.amount < other.amount

    def __le__(self, other: "Money") -> bool:
        self._require_same_currency(other)
        return self.amount <= other.amount

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        return (self.amount, self.currency) == (other.amount, other.currency)

    def __hash__(self) -> int:
        return hash((self.amount, self.currency))

    # ----- display -----------------------------------------------------------

    def __repr__(self) -> str:
        return f"Money({self.amount} {self.currency})"

    def __str__(self) -> str:
        return f"{self.amount:,} {self.currency}"

    # ----- helpers -----------------------------------------------------------

    def _require_same_currency(self, other: "Money") -> None:
        if not isinstance(other, Money):
            raise TypeError(
                f"expected Money, got {type(other).__name__}"
            )
        if self.currency != other.currency:
            raise ValueError(
                f"currency mismatch: {self.currency} vs {other.currency}"
            )