"""Exact finite decimal arithmetic independent of ambient Decimal context.

No tick schedule, strategy price formula, sizing or P&L calculation lives here.
Nonterminating ratios remain exact Fraction values rather than guessed tolerances.
"""

from decimal import Decimal
from fractions import Fraction


def decimal(value: str | int | Decimal) -> Decimal:
    """Accept exact inputs only; reject floats, booleans, NaN and infinity."""
    if type(value) not in (str, int, Decimal):
        raise TypeError("Use a string, integer or Decimal, never a binary float")
    result = Decimal(value)
    if not result.is_finite():
        raise ValueError("Value must be finite")
    return result


def _finite_decimal(value: Fraction) -> Decimal:
    """Construct an exact Decimal tuple without context-dependent rounding."""
    denominator = value.denominator
    twos = fives = 0
    while denominator % 2 == 0:
        denominator //= 2
        twos += 1
    while denominator % 5 == 0:
        denominator //= 5
        fives += 1
    if denominator != 1:
        raise ValueError("Nonterminating decimal; retain the exact ratio instead")
    scale = max(twos, fives)
    coefficient = value.numerator * 2 ** (scale - twos) * 5 ** (scale - fives)
    digits = tuple(int(c) for c in str(abs(coefficient)))
    return Decimal((int(coefficient < 0), digits, -scale))


def add(left: Decimal, right: Decimal) -> Decimal:
    return _finite_decimal(Fraction(decimal(left)) + Fraction(decimal(right)))


def subtract(left: Decimal, right: Decimal) -> Decimal:
    return _finite_decimal(Fraction(decimal(left)) - Fraction(decimal(right)))


def multiply(left: Decimal, right: Decimal) -> Decimal:
    return _finite_decimal(Fraction(decimal(left)) * Fraction(decimal(right)))


def exact_ratio(numerator: Decimal, denominator: Decimal) -> Fraction:
    """Return an exact ratio; zero remains an error, never an infinity sentinel."""
    return Fraction(decimal(numerator)) / Fraction(decimal(denominator))


def _tick_units(price: Decimal, tick: Decimal) -> Fraction:
    price, tick = decimal(price), decimal(tick)
    if price < 0 or tick <= 0:
        raise ValueError("Price must be nonnegative and supplied tick positive")
    return exact_ratio(price, tick)


def floor_to_tick(price: Decimal, tick: Decimal) -> Decimal:
    """Arithmetic primitive for a caller-supplied uniform increment."""
    units = _tick_units(price, tick)
    return multiply(Decimal(units.numerator // units.denominator), tick)


def ceil_to_tick(price: Decimal, tick: Decimal) -> Decimal:
    units = _tick_units(price, tick)
    return multiply(Decimal(-(-units.numerator // units.denominator)), tick)
