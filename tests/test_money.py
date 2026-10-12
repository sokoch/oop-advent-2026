"""Tests for the Money value object.

The tax engine's correctness ultimately depends on Money's arithmetic being
exact. These tests pin down rounding, currency checks, and float rejection.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.money import Money


# --- rounding ----------------------------------------------------------------

def test_half_up_rounds_0_point_4_down():
    assert Money(Decimal("335000.4")) == Money(Decimal("335000"))


def test_half_up_rounds_0_point_5_up():
    assert Money(Decimal("335000.5")) == Money(Decimal("335001"))


def test_half_up_rounds_0_point_4999_down():
    assert Money(Decimal("100.4999")) == Money(Decimal("100"))


def test_half_up_rounds_0_point_5001_up():
    assert Money(Decimal("100.5001")) == Money(Decimal("101"))


# --- construction ------------------------------------------------------------

def test_accepts_int():
    assert Money(Decimal("100")) == Money.of(100)


def test_accepts_str():
    assert Money(Decimal("100")) == Money.of("100")


def test_accepts_decimal():
    assert Money(Decimal("100")) == Money.of(Decimal("100"))


def test_rejects_float_via_of():
    with pytest.raises(TypeError, match="refuses floats"):
        Money.of(100.5)


def test_rejects_float_via_mul():
    with pytest.raises(TypeError, match="unsafe"):
        Money(Decimal("1000")) * 0.30


# --- arithmetic --------------------------------------------------------------

def test_add_same_currency():
    assert Money(Decimal("1000")) + Money(Decimal("500")) == Money(Decimal("1500"))


def test_sub_same_currency():
    assert Money(Decimal("1000")) - Money(Decimal("400")) == Money(Decimal("600"))


def test_mul_by_decimal():
    assert Money(Decimal("1000")) * Decimal("0.30") == Money(Decimal("300"))


def test_mul_by_str():
    assert Money(Decimal("1000")) * "0.30" == Money(Decimal("300"))


def test_mul_by_int():
    assert Money(Decimal("1000")) * 3 == Money(Decimal("3000"))


def test_rmul():
    """Scalar * Money should work too."""
    assert 3 * Money(Decimal("1000")) == Money(Decimal("3000"))


def test_add_different_currency_raises():
    with pytest.raises(ValueError, match="currency mismatch"):
        Money(Decimal("1000"), "UGX") + Money(Decimal("1000"), "USD")


def test_mul_by_money_raises():
    with pytest.raises(TypeError, match="meaningless"):
        Money(Decimal("1000")) * Money(Decimal("2"))


# --- comparison --------------------------------------------------------------

def test_lt():
    assert Money(Decimal("100")) < Money(Decimal("200"))
    assert not Money(Decimal("200")) < Money(Decimal("100"))


def test_le():
    assert Money(Decimal("100")) <= Money(Decimal("100"))
    assert Money(Decimal("100")) <= Money(Decimal("200"))


def test_eq_and_hash():
    a = Money(Decimal("100"))
    b = Money(Decimal("100"))
    assert a == b
    assert hash(a) == hash(b)
    assert {a, b} == {a}  # sets dedupe identical values


def test_lt_different_currency_raises():
    with pytest.raises(ValueError, match="currency mismatch"):
        Money(Decimal("100"), "UGX") < Money(Decimal("100"), "USD")


# --- display -----------------------------------------------------------------

def test_repr_is_parseable():
    assert repr(Money(Decimal("335000"))) == "Money(335000 UGX)"


def test_str_uses_thousands_separator():
    assert str(Money(Decimal("335000"))) == "335,000 UGX"


def test_str_small_amount():
    assert str(Money(Decimal("500"))) == "500 UGX"


# --- immutability ------------------------------------------------------------

def test_frozen_dataclass():
    """Money must be immutable — mutating .amount raises FrozenInstanceError."""
    from dataclasses import FrozenInstanceError

    m = Money(Decimal("100"))
    with pytest.raises(FrozenInstanceError):
        m.amount = Decimal("200")  # type: ignore[misc]