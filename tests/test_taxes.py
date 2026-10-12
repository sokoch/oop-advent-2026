"""Tests for PAYE, VAT, CIT, and the payroll pipeline.

Two test styles are used:

* **Example tests** — specific inputs and expected outputs. These pin the
  boundary values the assignment calls out (335,000 / 335,001 / 410,000 /
  485,000 / 10,000,000 / 10,000,001).

* **Property-based tests** (with `hypothesis`) — general claims that should
  hold for *any* valid input, not just the examples. Tax must be
  non-negative, monotonic in income, and never exceed income.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given, strategies as st, settings

from src.exceptions import InvalidIncomeError, NoScheduleForDateError
from src.money import Money
from src.payroll import LST, NSSF, PAYE, PayrollPipeline
from src.taxes import CIT, VAT, TaxContext


# --- helpers -----------------------------------------------------------------

AS_OF = date(2025, 1, 1)


def ctx(income: str, *, resident: bool = True, allowances: str = "0",
        lst: str = "0") -> TaxContext:
    return TaxContext(
        gross=Money(Decimal(income)),
        allowances=Money(Decimal(allowances)),
        resident=resident,
        as_of=AS_OF,
        lst=Money(Decimal(lst)),
    )


def paye(income: str, **kw) -> Money:
    return PAYE().calculate(ctx(income, **kw)).tax


# =========================================================================
# Boundary tests (the exact numbers the assignment lists)
# =========================================================================
@pytest.mark.parametrize(
    ("income", "expected"),
    [
        # Nil band
        ("0",        "0"),
        ("100000",   "0"),
        ("335000",   "0"),
        ("335001",   "0"),         # 20% * 1 = 0.20 rounds to 0
        # Band 2
        ("335005",   "1"),         # 20% * 5 = 1
        ("400000",   "13000"),     # 0.20 * 65,000
        ("410000",   "15000"),     # 0.20 * 75,000
        # Band 3
        ("410005",   "15001"),     # 15,000 + 0.25 * 5 = 15,001.25 → 15,001
        ("450000",   "25000"),     # 15,000 + 0.25 * 40,000
        ("485000",   "33750"),     # 15,000 + 0.25 * 75,000
        # Band 4
        ("485005",   "33751"),     # 33,750 + 0.30 * 5 = 33,751.50 → 33,752? see below
        ("1000000",  "188250"),    # 33,750 + 0.30 * 515,000
        ("10000000", "2888250"),   # 33,750 + 0.30 * 9,515,000
        # Surcharge band (> 10,000,000)
        ("10000001", "2888250"),   # + 0.10 * 1 = 0.10 rounds to 0
        ("10000100", "2888260"),   # + 0.10 * 100 = 10
    ],
)
def test_paye_boundaries(income: str, expected: str) -> None:
    """Exact boundary values from the assignment brief."""
    # Special-case 485,005: HALF_UP on 33,751.5 → 33,752.
    if income == "485005":
        expected = "33752"
    assert paye(income) == Money(Decimal(expected))


def test_paye_nil_below_threshold():
    assert paye("335000") == Money(Decimal("0"))


def test_paye_surcharge_applies_above_10m():
    """One extra shilling above 10M rounds to zero surcharge; 100 shillings is visible."""
    # 0.10 * 1 = 0.10 → rounds to 0
    assert paye("10000001") == Money(Decimal("2888250"))
    # 0.10 * 100 = 10
    assert paye("10000100") == Money(Decimal("2888260"))


def test_paye_non_resident_is_flat_30pct():
    assert paye("1000000", resident=False) == Money(Decimal("300000"))


def test_paye_includes_allowances():
    # gross 500,000 + allowances 300,000 - lst 0 = 800,000 chargeable
    # 33,750 + 0.30 * (800,000 - 485,000) = 33,750 + 94,500 = 128,250
    assert paye("500000", allowances="300000") == Money(Decimal("128250"))


def test_paye_lst_reduces_chargeable():
    # gross 500,000, lst 50,000 → chargeable 450,000
    # 15,000 + 0.25 * 40,000 = 25,000
    assert paye("500000", lst="50000") == Money(Decimal("25000"))


# =========================================================================
# Error handling
# =========================================================================
def test_negative_gross_raises():
    with pytest.raises(InvalidIncomeError):
        ctx("-1")


def test_negative_allowances_raises():
    with pytest.raises(InvalidIncomeError):
        ctx("100000", allowances="-1")


def test_schedule_date_before_available_raises():
    early = TaxContext(
        gross=Money(Decimal("500000")),
        as_of=date(2020, 1, 1),
    )
    with pytest.raises(NoScheduleForDateError):
        PAYE().calculate(early)


# =========================================================================
# Property-based tests — Hypothesis
# =========================================================================
# We constrain income to a realistic range so hypothesis explores
# interesting values without spending time on absurd magnitudes.
_income_strategy = st.integers(min_value=0, max_value=50_000_000)


@given(income=_income_strategy)
@settings(max_examples=200, deadline=None)
def test_tax_is_non_negative(income: int) -> None:
    result = PAYE().calculate(ctx(str(income)))
    assert result.tax >= Money(Decimal("0"))


@given(income=_income_strategy)
@settings(max_examples=200, deadline=None)
def test_tax_never_exceeds_income(income: int) -> None:
    """PAYE cannot exceed the chargeable income — sanity check."""
    result = PAYE().calculate(ctx(str(income)))
    assert result.tax <= Money(Decimal(str(income)))


@given(a=_income_strategy, b=_income_strategy)
@settings(max_examples=200, deadline=None)
def test_tax_is_monotonic_in_income(a: int, b: int) -> None:
    """If b >= a, tax(b) must be >= tax(a)."""
    lo, hi = sorted((a, b))
    tax_lo = PAYE().calculate(ctx(str(lo))).tax
    tax_hi = PAYE().calculate(ctx(str(hi))).tax
    assert tax_hi >= tax_lo


# =========================================================================
# Payroll pipeline
# =========================================================================
def test_pipeline_order_of_deductions():
    """Order: LST → NSSF employee → PAYE → net."""
    pipeline = PayrollPipeline(
        paye=PAYE(),
        nssf=NSSF(),
        lst=LST(Money(Decimal("10000"))),
    )
    result = pipeline.run(ctx("1000000"))

    assert result.lst == Money(Decimal("10000"))
    assert result.nssf_employee == Money(Decimal("50000"))
    # PAYE on (1,000,000 + 0 - 10,000) = 990,000
    # 33,750 + 0.30 * (990,000 - 485,000) = 33,750 + 151,500 = 185,250
    assert result.paye == Money(Decimal("185250"))
    assert result.nssf_employer == Money(Decimal("100000"))
    # net = 1,000,000 - 10,000 - 50,000 - 185,250 = 754,750
    assert result.net == Money(Decimal("754750"))


def test_pipeline_without_lst():
    """When LST is None, no LST is deducted."""
    pipeline = PayrollPipeline(paye=PAYE(), nssf=NSSF(), lst=None)
    result = pipeline.run(ctx("1000000"))
    assert result.lst == Money(Decimal("0"))


# =========================================================================
# VAT
# =========================================================================
def test_vat_standard_only():
    invoices = [{"type": "standard", "amount": Money(Decimal("1000000"))}]
    out = VAT.compute_invoices(invoices)
    assert out["output_vat"] == Money(Decimal("180000"))
    assert out["input_vat"] == Money(Decimal("0"))
    assert out["net_vat"] == Money(Decimal("180000"))


def test_vat_zero_and_exempt_produce_no_output_vat():
    invoices = [
        {"type": "zero",   "amount": Money(Decimal("1000000"))},
        {"type": "exempt", "amount": Money(Decimal("500000"))},
    ]
    out = VAT.compute_invoices(invoices)
    assert out["output_vat"] == Money(Decimal("0"))
    assert out["net_vat"] == Money(Decimal("0"))


def test_vat_input_vat_offsets_output():
    invoices = [
        {"type": "standard", "amount": Money(Decimal("1000000")),
         "input_vat": Money(Decimal("50000"))},
    ]
    out = VAT.compute_invoices(invoices)
    assert out["output_vat"] == Money(Decimal("180000"))
    assert out["input_vat"] == Money(Decimal("50000"))
    assert out["net_vat"] == Money(Decimal("130000"))


def test_vat_unknown_type_raises():
    with pytest.raises(InvalidIncomeError, match="unknown type"):
        VAT.compute_invoices([{"type": "weird", "amount": Money(Decimal("100"))}])


# =========================================================================
# CIT
# =========================================================================
def test_cit_30_percent():
    """Revenue 100M, deductions 20M, chargeable 80M, tax 24M."""
    context = TaxContext(
        gross=Money(Decimal("100000000")),
        allowances=Money(Decimal("20000000")),
    )
    result = CIT().calculate(context)
    assert result.tax == Money(Decimal("24000000"))


def test_cit_deductions_exceed_revenue_raises():
    context = TaxContext(
        gross=Money(Decimal("1000000")),
        allowances=Money(Decimal("2000000")),
    )
    with pytest.raises(InvalidIncomeError, match="exceed revenue"):
        CIT().calculate(context)


def test_cit_zero_revenue():
    context = TaxContext(
        gross=Money(Decimal("0")),
        allowances=Money(Decimal("0")),
    )
    assert CIT().calculate(context).tax == Money(Decimal("0"))