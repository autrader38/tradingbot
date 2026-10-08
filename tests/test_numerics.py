from decimal import Decimal, localcontext, ROUND_UP
from fractions import Fraction
import unittest

from tradingbot_backtest.numerics import decimal, add, subtract, multiply, exact_ratio, floor_to_tick, ceil_to_tick

D = Decimal


class NumericTests(unittest.TestCase):
    def test_decimal_input_is_exact(self):
        self.assertEqual(add(decimal('0.1'), decimal('0.2')), D('0.3'))
        for value in [0.1, True, None]:
            with self.subTest(value=value), self.assertRaises(TypeError):
                decimal(value)
        for value in ['NaN', 'Infinity', '-Infinity']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                decimal(value)

    def test_arithmetic_preserves_precision_independent_of_global_context(self):
        with localcontext() as context:
            context.prec = 2
            context.rounding = ROUND_UP
            self.assertEqual(add(D('123456789.01'), D('.002')), D('123456789.012'))
            self.assertEqual(subtract(D('1.00001'), D('1')), D('.00001'))
            self.assertEqual(multiply(D('1.2345'), D('.0001')), D('.00012345'))
            self.assertEqual(multiply(D('-1.25'), D('2')), D('-2.50'))
            self.assertEqual(add(D('1'), D('-1')), D('0'))

    def test_tick_boundaries_and_subcent_increments(self):
        cases = [('1.4925', '.01', '1.49', '1.50'),
                 ('1.4925', '.0001', '1.4925', '1.4925'),
                 ('2.005', '.001', '2.005', '2.005'),
                 ('1.13', '.05', '1.10', '1.15'),
                 ('0', '.01', '0', '0')]
        with localcontext() as context:
            context.prec = 2
            for price, tick, down, up in cases:
                with self.subTest(price=price, tick=tick):
                    self.assertEqual(floor_to_tick(D(price), D(tick)), D(down))
                    self.assertEqual(ceil_to_tick(D(price), D(tick)), D(up))
        for tick in ['0', '-.01', 'NaN']:
            with self.subTest(tick=tick), self.assertRaises(ValueError):
                floor_to_tick(D('1'), D(tick))
        with self.assertRaises(ValueError):
            ceil_to_tick(D('-1'), D('.01'))

    def test_nonterminating_ratio_stays_exact_and_zero_is_not_infinity(self):
        self.assertEqual(exact_ratio(D('1'), D('3')), Fraction(1, 3))
        with self.assertRaises(ZeroDivisionError):
            exact_ratio(D('100'), D('0'))
