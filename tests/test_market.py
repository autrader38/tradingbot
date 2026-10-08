from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
from zoneinfo import ZoneInfo

from tradingbot_backtest.market import IntervalClassification as Kind, MarketInterval, validate_sequence

D = Decimal


def candle(**overrides):
    fields = dict(security_id="test-id", ticker="TEST", source_id="offline-fixture",
                  timestamp=datetime(2026, 7, 1, 13, 30, tzinfo=timezone.utc),
                  classification=Kind.TRADED, open=D("1.00"), high=D("1.20"),
                  low=D("0.95"), close=D("1.10"), volume=D("100"))
    fields.update(overrides)
    return MarketInterval(**fields)


class MarketTests(unittest.TestCase):
    def test_valid_traded_candle_and_completion(self):
        value = candle()
        self.assertEqual(value.classification, Kind.TRADED)
        self.assertEqual(value.end, value.timestamp + timedelta(minutes=1))
        self.assertEqual(value.open, D("1.00"))
        with self.assertRaises(FrozenInstanceError):
            value.volume = D("0")

    def test_invalid_ohlc_relationships(self):
        for fields in [dict(high=D("0.90")), dict(low=D("1.21")),
                       dict(open=D("1.30")), dict(close=D("0.90"))]:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                candle(**fields)

    def test_nonpositive_and_nonfinite_price(self):
        for price in [D("0"), D("-1"), D("NaN"), D("Infinity")]:
            with self.subTest(price=price), self.assertRaises(ValueError):
                candle(open=price)

    def test_volume_and_numeric_types(self):
        for volume in [D("-1"), D("NaN"), D("Infinity")]:
            with self.subTest(volume=volume), self.assertRaises(ValueError):
                candle(volume=volume)
        with self.assertRaises(TypeError):
            candle(open=1.0)
        with self.assertRaises(TypeError):
            candle(volume=100)
        # Nonnegative-volume validation does not infer provider classification.
        self.assertEqual(candle(volume=D("0")).classification, Kind.TRADED)

    def test_verified_no_trade_has_no_synthetic_prices(self):
        value = candle(classification=Kind.NO_TRADE, open=None, high=None,
                       low=None, close=None, volume=D("0"), no_trade_verified=True)
        self.assertIsNone(value.close)
        self.assertEqual(value.volume, D("0"))
        for field in ("open", "high", "low", "close"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                replace(value, **{field: D("1")})
        for fields in [dict(no_trade_verified=False), dict(volume=D("1")),
                       dict(volume=None), dict(volume=0),
                       dict(data_quality_reason="unexplained absence")]:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                replace(value, **fields)

    def test_missing_invalid_are_distinct_and_unpriced(self):
        for kind in (Kind.MISSING, Kind.INVALID):
            value = candle(classification=kind, open=None, high=None, low=None,
                           close=None, volume=None, data_quality_reason="source unavailable")
            self.assertEqual(value.classification, kind)
            self.assertNotEqual(value.classification, Kind.NO_TRADE)
            with self.assertRaises(ValueError):
                replace(value, volume=D("0"))
            with self.assertRaises(ValueError):
                replace(value, no_trade_verified=True)
            with self.assertRaises(ValueError):
                replace(value, data_quality_reason=None)

    def test_classification_is_explicit(self):
        with self.assertRaises(TypeError):
            candle(classification="NO_TRADE")
        with self.assertRaises(ValueError):
            candle(no_trade_verified=True)
        with self.assertRaises(ValueError):
            candle(data_quality_reason="bad source")

    def test_timestamp_validation(self):
        for value in [datetime(2026, 7, 1, 9, 30), "2026-07-01",
                      datetime(2026, 7, 1, 13, 30, 1, tzinfo=timezone.utc),
                      datetime(2026, 3, 8, 2, 30, tzinfo=ZoneInfo("America/New_York"))]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                candle(timestamp=value)

    def test_sequence_rejects_unresolved_duplicates_and_reverse_order(self):
        first = candle()
        second = replace(first, timestamp=first.timestamp+timedelta(minutes=1))
        validate_sequence([first, second])
        for values in [[first, first], [second, first], [first, replace(first, close=D("1.15"))]]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                validate_sequence(values)
        validate_sequence([first, replace(first, security_id="other", ticker="OTHER")])
        # Same instant represented in a different timezone is still a duplicate.
        with self.assertRaises(ValueError):
            validate_sequence([first, replace(first, timestamp=first.timestamp.astimezone(ZoneInfo("America/New_York")))])
