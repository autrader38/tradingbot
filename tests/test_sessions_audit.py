from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, time, timezone
from decimal import Decimal
import unittest

from tradingbot_backtest.audit import AuditRecord
from tradingbot_backtest.config import FROZEN_V1
from tradingbot_backtest.codes import ReasonCode
from tradingbot_backtest.sessions import NEW_YORK, SessionCalendar, SessionPeriod, TradingSession
from tradingbot_backtest.states import StrategyState


def session(day, close_hour=16):
    return TradingSession(day, datetime.combine(day, time(9, 30), NEW_YORK),
                          datetime.combine(day, time(close_hour), NEW_YORK), 'verified-test-fixture')


class CalendarFixture:
    """Explicit supplied dates only: unknown is not silently a holiday."""
    def __init__(self, values):
        self.values = values

    def session_for(self, trading_date):
        return self.values[trading_date]


class SessionAndAuditTests(unittest.TestCase):
    def test_regular_premarket_boundaries(self):
        day = date(2026, 7, 1)
        value = session(day)
        checks = [(time(3, 59), SessionPeriod.OUTSIDE),
                  (time(4), SessionPeriod.PREMARKET_VOLUME_CONTEXT),
                  (time(9, 29), SessionPeriod.PREMARKET_VOLUME_CONTEXT),
                  (time(9, 30), SessionPeriod.REGULAR),
                  (time(15, 59), SessionPeriod.REGULAR),
                  (time(16), SessionPeriod.OUTSIDE)]
        for clock, expected in checks:
            with self.subTest(clock=clock):
                self.assertEqual(value.period_at(datetime.combine(day, clock, NEW_YORK),
                                                 premarket_start=FROZEN_V1.premarket_volume_history_start_et), expected)

    def test_early_close_and_calendar_holiday_contract(self):
        day = date(2026, 11, 27)
        early = session(day, 13)
        calendar: SessionCalendar = CalendarFixture({day: early, date(2026, 11, 26): None})
        self.assertEqual(calendar.session_for(day).close.hour, 13)
        self.assertIsNone(calendar.session_for(date(2026, 11, 26)))
        with self.assertRaises(KeyError):
            calendar.session_for(date(2026, 11, 25))
        self.assertEqual(early.period_at(datetime.combine(day, time(13), NEW_YORK),
                                        premarket_start=time(4)), SessionPeriod.OUTSIDE)

    def test_dst_offsets_and_unknown_boundaries(self):
        winter, summer = session(date(2026, 1, 5)), session(date(2026, 7, 1))
        self.assertEqual(winter.open.astimezone(timezone.utc).hour, 14)
        self.assertEqual(summer.open.astimezone(timezone.utc).hour, 13)
        with self.assertRaises(ValueError):
            replace(summer, close=summer.open)
        with self.assertRaises(ValueError):
            replace(summer, open=summer.open.replace(tzinfo=None))
        with self.assertRaises(ValueError):
            replace(summer, trading_date=date(2026, 7, 2))

    def test_audit_identity_interval_and_exact_details(self):
        at = datetime(2026, 7, 1, 13, 30, tzinfo=timezone.utc)
        record = AuditRecord('run-1', 'fixture-v1', 'data-validation', at,
                             security_id='test-id', ticker='TEST',
                             state=StrategyState.ELIGIBLE, reason=ReasonCode.INVALID_DATA,
                             interval_start=at, interval_end=at.replace(minute=31),
                             details=(('raw-price', 'corrupt'), ('reported-volume', Decimal('100'))))
        self.assertEqual(record.reason.value, 'INVALID_DATA')
        with self.assertRaises(FrozenInstanceError):
            record.run_id = 'changed'
        for details in [(('price', 1.1),), (('x', 1), ('x', 2)), (('x', Decimal('NaN')), )]:
            with self.subTest(details=details), self.assertRaises((TypeError, ValueError)):
                replace(record, details=details)
        with self.assertRaises(ValueError):
            replace(record, interval_end=None)
        with self.assertRaises(TypeError):
            replace(record, state='ELIGIBLE')
        with self.assertRaises(ValueError):
            replace(record, recorded_at=None)
        with self.assertRaises(ValueError):
            replace(record, security_id='')
