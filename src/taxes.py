"""Tax domain classes for the Uganda Revenue Authority (URA) rules.

Scope
-----
This module implements:

* ``TaxContext`` — the immutable inputs to a tax calculation.
* ``TaxResult`` — the immutable output: an amount and a breakdown.
* ``Tax`` — abstract base with a single ``calculate(ctx) -> TaxResult`` method.
* ``PAYE`` — Pay As You Earn for resident and non-resident monthly earners.
* ``VAT`` — Value Added Tax for standard-rated, zero-rated, and exempt supplies.
* ``CIT`` — Corporate Income Tax for resident companies.

Design
------
* Money is ``Decimal``-only via the ``Money`` value object (see ``src/money.py``).
* Every tax is stateless. All state lives in ``TaxContext``; every call
  returns a fresh ``TaxResult``. This makes taxes trivially testable and
  parallelisable.
* Bands are data, not code. Adding a new schedule means adding a
  ``_BandSchedule`` instance, not editing a branch.

Tax schedule reference
----------------------
Rates are based on the URA PAYE schedule for resident individuals, monthly.
Bands (monthly, in UGX):

    Nil              up to             335,000
    20%  of excess over               335,000  up to  410,000
    15,000  + 25% of excess over      410,000  up to  485,000
    33,750  + 30% of excess over      485,000  up to 10,000,000
    Above 10,000,000: add 10% surcharge on the excess.

Non-resident schedule: 30% flat on chargeable income (see `PAYE_NON_RESIDENT`
below). Confirm against the current URA notice before use in production.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Sequence

from src.exceptions import (
    InvalidIncomeError,
    NoScheduleForDateError,
)
from src.money import Money


# =========================================================================
# Context and result
# =========================================================================
@dataclass(frozen=True)
class TaxContext:
    """The immutable inputs to a single tax calculation.

    Attributes
    ----------
    gross:
        Gross pay for the period (monthly for PAYE).
    allowances:
        Non-cash or cash allowances added to gross before tax.
    resident:
        True for resident individuals, False for non-residents.
    as_of:
        Assessment date. Used to look up the correct schedule vintage.
    lst:
        Optional Local Service Tax, already deducted from chargeable income.
    """

    gross: Money
    allowances: Money = field(default_factory=lambda: Money(Decimal("0")))
    resident: bool = True
    as_of: date = field(default_factory=date.today)
    lst: Money = field(default_factory=lambda: Money(Decimal("0")))

    def __post_init__(self) -> None:
        # Money is already validated by its own __post_init__. We only add
        # tax-specific validation: gross and allowances must not be negative.
        if self.gross < Money(Decimal("0")):
            raise InvalidIncomeError(
                f"gross must be non-negative, got {self.gross}"
            )
        if self.allowances < Money(Decimal("0")):
            raise InvalidIncomeError(
                f"allowances must be non-negative, got {self.allowances}"
            )

    @property
    def chargeable(self) -> Money:
        """Gross + allowances - LST. This is the base the bands apply to."""
        return self.gross + self.allowances - self.lst


@dataclass(frozen=True)
class TaxResult:
    """The immutable output of a tax calculation.

    Attributes
    ----------
    tax:
        The total tax payable as a ``Money`` value.
    breakdown:
        Component amounts, keyed by short labels. Useful for pay slips.
    """

    tax: Money
    breakdown: dict[str, Money] = field(default_factory=dict)


# =========================================================================
# Band schedule — data, not code
# =========================================================================
@dataclass(frozen=True)
class Band:
    """One row of a progressive tax schedule.

    Attributes
    ----------
    lower:
        Income above which this band applies (exclusive lower bound).
    upper:
        Income up to which this band applies (inclusive upper bound).
        Use ``Decimal("Infinity")`` for the top band.
    rate:
        Marginal rate on income within this band.
    base:
        Cumulative tax on all income below ``lower``. Precomputed so we
        never have to loop over prior bands.
    """

    lower: Decimal
    upper: Decimal
    rate: Decimal
    base: Decimal


@dataclass(frozen=True)
class BandSchedule:
    """A named, dated progressive schedule."""

    name: str
    as_of: date
    bands: Sequence[Band]

    def tax_on(self, income: Decimal) -> tuple[Decimal, dict[str, Decimal]]:
        """Return (total tax, per-band tax) for the given chargeable income."""
        if income < 0:
            raise InvalidIncomeError(f"income must be non-negative, got {income}")

        total = Decimal("0")
        breakdown: dict[str, Decimal] = {}

        for i, band in enumerate(self.bands):
            if income <= band.lower:
                break
            slice_amount = min(income, band.upper) - band.lower
            if slice_amount <= 0:
                continue
            component = band.rate * slice_amount
            total += component
            breakdown[f"band_{i + 1}_{band.rate}"] = component

        return total, breakdown


# --- the schedule we implement ----------------------------------------------

# Resident monthly PAYE bands, from the assignment brief.
_RESIDENT_BANDS = (
    Band(
        lower=Decimal("0"),
        upper=Decimal("335000"),
        rate=Decimal("0.00"),
        base=Decimal("0"),
    ),
    Band(
        lower=Decimal("335000"),
        upper=Decimal("410000"),
        rate=Decimal("0.20"),
        base=Decimal("0"),
    ),
    Band(
        lower=Decimal("410000"),
        upper=Decimal("485000"),
        rate=Decimal("0.25"),
        base=Decimal("15000"),
    ),
    Band(
        lower=Decimal("485000"),
        upper=Decimal("10000000"),
        rate=Decimal("0.30"),
        base=Decimal("33750"),
    ),
)

PAYE_RESIDENT_SCHEDULE = BandSchedule(
    name="PAYE resident monthly (URA)",
    as_of=date(2024, 7, 1),  # placeholder — update to the notice date you cite
    bands=_RESIDENT_BANDS,
)

SURCHARGE_THRESHOLD = Decimal("10000000")
SURCHARGE_RATE = Decimal("0.10")


# =========================================================================
# Abstract base
# =========================================================================
class Tax(ABC):
    """Abstract tax.

    Subclasses implement ``calculate(ctx) -> TaxResult``. The base class
    intentionally contains no shared logic — every tax has a different
    enough shape that a common ``calculate`` template would be a false
    abstraction. Inheritance here encodes a contract, not behaviour.
    """

    name: str = "tax"

    @abstractmethod
    def calculate(self, ctx: TaxContext) -> TaxResult:
        """Return the tax payable for the given context."""

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}()"


# =========================================================================
# PAYE
# =========================================================================
class PAYE(Tax):
    """Pay As You Earn for monthly employment income.

    Supports resident and non-resident schedules. The resident schedule
    uses progressive bands plus a 10% surcharge above 10,000,000 UGX.
    """

    name = "paye"

    def __init__(
        self,
        schedule: BandSchedule = PAYE_RESIDENT_SCHEDULE,
        *,
        surcharge_threshold: Decimal = SURCHARGE_THRESHOLD,
        surcharge_rate: Decimal = SURCHARGE_RATE,
    ) -> None:
        self.schedule = schedule
        self.surcharge_threshold = surcharge_threshold
        self.surcharge_rate = surcharge_rate

    def calculate(self, ctx: TaxContext) -> TaxResult:
        if ctx.as_of < self.schedule.as_of:
            raise NoScheduleForDateError(
                f"no {self.schedule.name} schedule available before "
                f"{self.schedule.as_of}; got {ctx.as_of}"
            )

        if not ctx.resident:
            return self._calculate_non_resident(ctx)
        return self._calculate_resident(ctx)

    # ----- resident ----------------------------------------------------------

    def _calculate_resident(self, ctx: TaxContext) -> TaxResult:
        chargeable = ctx.chargeable.amount
        band_tax, per_band = self.schedule.tax_on(chargeable)

        # Surcharge on income above the threshold.
        surcharge = Decimal("0")
        if chargeable > self.surcharge_threshold:
            surcharge = self.surcharge_rate * (
                chargeable - self.surcharge_threshold
            )

        total = band_tax + surcharge
        breakdown = {
            **{k: Money(v) for k, v in per_band.items()},
            "surcharge": Money(surcharge),
            "total": Money(total),
        }
        return TaxResult(tax=Money(total), breakdown=breakdown)

    # ----- non-resident ------------------------------------------------------

    def _calculate_non_resident(self, ctx: TaxContext) -> TaxResult:
        # Flat 30% on chargeable income. Confirm against the current URA
        # non-resident schedule before relying on this in production.
        rate = Decimal("0.30")
        tax = rate * ctx.chargeable.amount
        return TaxResult(
            tax=Money(tax),
            breakdown={"non_resident_30pct": Money(tax)},
        )


# =========================================================================
# VAT
# =========================================================================
class VAT(Tax):
    """Value Added Tax.

    Unlike PAYE and CIT, VAT is not computed per-person. It is computed
    per **invoice list**, so ``calculate(ctx)`` is not the right entry
    point. We still implement it to satisfy the ``Tax`` contract, but the
    real API is the ``compute_invoices`` classmethod.
    """

    name = "vat"
    STANDARD_RATE = Decimal("0.18")

    def calculate(self, ctx: TaxContext) -> TaxResult:
        raise NotImplementedError(
            "VAT is not per-taxpayer; use VAT.compute_invoices(invoices)."
        )

    @classmethod
    def compute_invoices(
        cls,
        invoices: Sequence[dict],
    ) -> dict[str, Money]:
        """Compute output VAT, input VAT, and net VAT payable.

        Each invoice is a dict with:
            * ``type``  — "standard", "zero", or "exempt"
            * ``amount`` — Money (net of VAT)
            * ``input_vat`` — Money, the VAT paid on inputs attributable
              to this invoice. Zero for exempt supplies by default.
        """
        output_vat = Decimal("0")
        input_vat = Decimal("0")

        for i, inv in enumerate(invoices):
            kind = inv["type"]
            amount = inv["amount"].amount
            input_v = inv.get("input_vat", Money(Decimal("0"))).amount

            if kind == "standard":
                output_vat += cls.STANDARD_RATE * amount
                input_vat += input_v
            elif kind in {"zero", "exempt"}:
                # No output VAT on zero-rated or exempt supplies.
                # Input VAT on exempt supplies is generally not recoverable;
                # we include it only if the caller explicitly passes it.
                if kind == "zero":
                    input_vat += input_v
            else:
                raise InvalidIncomeError(
                    f"invoice {i}: unknown type {kind!r}"
                )

        net = output_vat - input_vat
        return {
            "output_vat": Money(output_vat),
            "input_vat": Money(input_vat),
            "net_vat": Money(net),
        }


# =========================================================================
# Corporate Income Tax
# =========================================================================
class CIT(Tax):
    """Corporate Income Tax for resident companies.

    Chargeable income = revenue - allowable deductions. Rate: 30%.
    """

    name = "cit"
    RATE = Decimal("0.30")

    def calculate(self, ctx: TaxContext) -> TaxResult:
        # Reuse TaxContext: `gross` = revenue, `allowances` = deductions.
        chargeable = ctx.gross - ctx.allowances
        if chargeable < Money(Decimal("0")):
            raise InvalidIncomeError(
                f"deductions exceed revenue: {chargeable}"
            )
        tax = self.RATE * chargeable.amount
        return TaxResult(
            tax=Money(tax),
            breakdown={
                "chargeable": chargeable,
                f"cit_{self.RATE}": Money(tax),
            },
        )

# =========================================================================
# Extension (Option B) — TaxPlanner
# =========================================================================
@dataclass(frozen=True)
class ScenarioResult:
    """The result of one remuneration scenario."""

    label: str
    gross: Money
    cit: Money
    paye: Money
    withholding: Money
    net: Money

    def __str__(self) -> str:
        return (
            f"{self.label}:\n"
            f"  gross       = {self.gross}\n"
            f"  CIT         = {self.cit}\n"
            f"  PAYE        = {self.paye}\n"
            f"  withholding = {self.withholding}\n"
            f"  net         = {self.net}"
        )


class TaxPlanner:
    """Compare two remuneration scenarios on an after-tax basis.

    Scenario A — **Salary**:
        Company earns `company_profit`. Owner takes `gross` as salary.
        * PAYE is computed on the salary (via PAYE).
        * NSSF is ignored for simplicity; add it if needed.
        * Company still pays CIT on `company_profit`.

    Scenario B — **Dividend**:
        Company earns `company_profit`, pays CIT on the whole profit,
        then distributes `gross` as a dividend from the post-tax pool.
        * Withholding tax at 10% applies to the dividend paid to the owner.

    The comparison is on **net cash to the owner**, which is the number
    that matters to the recipient. Both scenarios assume a resident
    individual and a resident company.

    Parameters
    ----------
    paye:
        Instance of PAYE used for the salary path.
    cit:
        Instance of CIT used for both scenarios.
    withholding_rate:
        Dividend withholding rate. Default 10%.
    """

    def __init__(
        self,
        paye: PAYE | None = None,
        cit: CIT | None = None,
        *,
        withholding_rate: Decimal = Decimal("0.10"),
    ) -> None:
        self.paye = paye or PAYE()
        self.cit = cit or CIT()
        self.withholding_rate = withholding_rate

    def salary_scenario(
        self,
        company_profit: Money,
        gross_salary: Money,
        *,
        as_of: date,
    ) -> ScenarioResult:
        """Owner takes `gross_salary` as salary; company pays CIT on profit."""
        cit_ctx = TaxContext(gross=company_profit, as_of=as_of)
        cit_result = self.cit.calculate(cit_ctx)

        paye_ctx = TaxContext(gross=gross_salary, as_of=as_of)
        paye_result = self.paye.calculate(paye_ctx)

        net = gross_salary - paye_result.tax
        return ScenarioResult(
            label="Salary",
            gross=gross_salary,
            cit=cit_result.tax,
            paye=paye_result.tax,
            withholding=Money(Decimal("0")),
            net=net,
        )

    def dividend_scenario(
        self,
        company_profit: Money,
        dividend: Money,
        *,
        as_of: date,
    ) -> ScenarioResult:
        """Owner takes `dividend` as a dividend from post-CIT profit."""
        cit_ctx = TaxContext(gross=company_profit, as_of=as_of)
        cit_result = self.cit.calculate(cit_ctx)

        withholding = Money(self.withholding_rate * dividend.amount)
        net = dividend - withholding
        return ScenarioResult(
            label="Dividend",
            gross=dividend,
            cit=cit_result.tax,
            paye=Money(Decimal("0")),
            withholding=withholding,
            net=net,
        )

    def compare(
        self,
        company_profit: Money,
        amount: Money,
        *,
        as_of: date,
    ) -> dict[str, ScenarioResult | Money]:
        """Return both scenarios and the difference in net cash to the owner."""
        salary = self.salary_scenario(company_profit, amount, as_of=as_of)
        dividend = self.dividend_scenario(company_profit, amount, as_of=as_of)
        return {
            "salary": salary,
            "dividend": dividend,
            "difference": salary.net - dividend.net,
        }