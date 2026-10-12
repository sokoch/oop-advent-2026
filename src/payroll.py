"""Payroll pipeline for the Uganda tax rules.

Wires NSSF, LST, and PAYE together in the order required by URA practice.
The order matters and is documented below; the tests in
``tests/test_taxes.py`` pin it down so it can't drift.

Order of deductions (monthly, employee side)
--------------------------------------------
1. **Local Service Tax (LST)** — a fixed local-government levy. Deducted
   from chargeable income *before* PAYE, per URA practice. Configurable
   because LST rates vary by district and by annual income bracket.
2. **NSSF employee contribution (5% of gross)** — a statutory retirement
   contribution deducted before PAYE.
3. **PAYE** — computed on (gross + allowances − LST). NSSF employee
   contributions are treated as part of the employee's earnings for PAYE
   purposes in the current URA schedule; we do not subtract them.
4. **Net pay** = gross + allowances − LST − NSSF employee − PAYE.

Employer costs
--------------
NSSF employer contribution (10% of gross) is *not* deducted from the
employee's pay; it is an additional cost the employer pays. We expose it
in the pipeline output for reporting.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from src.money import Money
from src.taxes import PAYE, Tax, TaxContext, TaxResult


# =========================================================================
# NSSF
# =========================================================================
class NSSF(Tax):
    """National Social Security Fund contributions.

    Rates from the URA / NSSF baseline:
    * Employee: 5% of gross
    * Employer: 10% of gross

    Both are statutory contributions on gross pay. The employee's share
    reduces net pay; the employer's share is a cost of employment.
    """

    name = "nssf"
    EMPLOYEE_RATE = Decimal("0.05")
    EMPLOYER_RATE = Decimal("0.10")

    def calculate(self, ctx: TaxContext) -> TaxResult:
        # NSSF is on gross pay. We do not include `allowances` because the
        # assignment brief specifies "5% of gross" — if a future rule
        # extends the base, change it here and the tests will catch it.
        employee = self.EMPLOYEE_RATE * ctx.gross.amount
        employer = self.EMPLOYER_RATE * ctx.gross.amount
        return TaxResult(
            tax=Money(employee),
            breakdown={
                "nssf_employee": Money(employee),
                "nssf_employer": Money(employer),
            },
        )


# =========================================================================
# Local Service Tax
# =========================================================================
class LST(Tax):
    """Local Service Tax — a fixed levy set by the local government.

    Modelled as an explicit configurable amount because LST rates vary
    by district and by income bracket. Making it an explicit amount
    keeps this class honest: the caller supplies the amount the local
    authority prescribes.
    """

    name = "lst"

    def __init__(self, amount: Money) -> None:
        if amount < Money(Decimal("0")):
            raise ValueError(f"LST amount must be non-negative, got {amount}")
        self.amount = amount

    def calculate(self, ctx: TaxContext) -> TaxResult:
        return TaxResult(
            tax=self.amount,
            breakdown={"lst": self.amount},
        )


# =========================================================================
# Payroll pipeline
# =========================================================================
@dataclass(frozen=True)
class PayrollResult:
    """The full breakdown for one employee-month."""

    gross: Money
    allowances: Money
    lst: Money
    nssf_employee: Money
    nssf_employer: Money
    paye: Money
    net: Money
    paye_breakdown: dict[str, Money]

    def __str__(self) -> str:
        return (
            f"PayrollResult(\n"
            f"  gross          = {self.gross}\n"
            f"  allowances     = {self.allowances}\n"
            f"  lst            = {self.lst}\n"
            f"  nssf_employee  = {self.nssf_employee}\n"
            f"  nssf_employer  = {self.nssf_employer}\n"
            f"  paye           = {self.paye}\n"
            f"  net            = {self.net}\n"
            f")"
        )


class PayrollPipeline:
    """Compute LST, NSSF, PAYE, and net pay for one employee-month.

    Composition, not inheritance: the pipeline *has a* PAYE, an NSSF, and
    optionally an LST. It is not itself a Tax — it is the thing that calls
    taxes in the correct order.
    """

    def __init__(
        self,
        paye: PAYE | None = None,
        nssf: NSSF | None = None,
        lst: LST | None = None,
    ) -> None:
        self.paye = paye or PAYE()
        self.nssf = nssf or NSSF()
        self.lst = lst

    def run(self, ctx: TaxContext) -> PayrollResult:
        """Compute the full payroll breakdown for a single month.

        Raises
        ------
        TaxError
            Any validation error from the underlying taxes propagates.
        """
        # 1. LST (optional). If not configured, deduct nothing.
        if self.lst is not None:
            lst_amount = self.lst.calculate(ctx).tax
        else:
            lst_amount = Money(Decimal("0"))

        # 2. NSSF employee contribution, on gross.
        nssf_result = self.nssf.calculate(ctx)
        nssf_employee = nssf_result.breakdown["nssf_employee"]
        nssf_employer = nssf_result.breakdown["nssf_employer"]

        # 3. PAYE on (gross + allowances - LST).
        paye_ctx = TaxContext(
            gross=ctx.gross,
            allowances=ctx.allowances,
            resident=ctx.resident,
            as_of=ctx.as_of,
            lst=lst_amount,
        )
        paye_result = self.paye.calculate(paye_ctx)

        # 4. Net pay.
        net = (
            ctx.gross
            + ctx.allowances
            - lst_amount
            - nssf_employee
            - paye_result.tax
        )

        return PayrollResult(
            gross=ctx.gross,
            allowances=ctx.allowances,
            lst=lst_amount,
            nssf_employee=nssf_employee,
            nssf_employer=nssf_employer,
            paye=paye_result.tax,
            net=net,
            paye_breakdown=paye_result.breakdown,
        )