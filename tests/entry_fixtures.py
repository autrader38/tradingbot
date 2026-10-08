"""Independent supplied histories and a real Phase 4 handoff for Phase 5 tests."""

from datetime import date, datetime, timedelta
from decimal import Decimal as D

from tradingbot_backtest.ab_detector import EligibilityInputs
from tradingbot_backtest.d_detector import DDetector
from tradingbot_backtest.entry_context import ContextMinute, WeeklyBar, WeeklyClassification as W, WeeklyHistory
from tradingbot_backtest.entry_validation import EntryContext, OpeningEligibility
from tradingbot_backtest.market import IntervalClassification as K, MarketInterval
from tradingbot_backtest.numerics import multiply
from tradingbot_backtest.sessions import NEW_YORK, TradingSession


class Calendar:
    def __init__(self, close_hour=16, holidays=()):
        self.close_hour, self.holidays = close_hour, frozenset(holidays)

    def session_for(self, day):
        if day.weekday() >= 5 or day in self.holidays:
            return None
        return TradingSession(day, datetime.combine(day, datetime.min.time(), NEW_YORK).replace(hour=9, minute=30),
                              datetime.combine(day, datetime.min.time(), NEW_YORK).replace(hour=self.close_hour),
                              'verified-fixture-calendar')


def minute(at, low='0.99', high='1.01', close='1', volume='100', opening=None,
           kind=K.TRADED, cause='missing source candle'):
    fields = dict(security_id='security', ticker='TEST', timestamp=at, classification=kind, source_id='fixture')
    if kind == K.TRADED:
        fields.update(open=D(opening if opening is not None else close), high=D(high), low=D(low),
                      close=D(close), volume=D(volume))
    elif kind == K.NO_TRADE:
        fields.update(volume=D(0), no_trade_verified=True)
    else:
        fields.update(data_quality_reason=cause)
    return MarketInterval(**fields)


def weekly_history(opening, calendar, count=12, highs=None, basis='fixture-basis'):
    current = opening.date() - timedelta(days=opening.weekday())
    weeks = []
    week = current - timedelta(days=7)
    while len(weeks) < count:
        sessions = [calendar.session_for(week + timedelta(days=i)) for i in range(5)]
        sessions = [s for s in sessions if s is not None]
        if sessions:
            weeks.append((week, max(s.close for s in sessions)))
        week -= timedelta(days=7)
    weeks.reverse()
    bars = tuple(WeeklyBar('security', week, close, close, 'weekly-fixture', basis, W.VALID,
                           D(highs[i]) if highs is not None else D('.8'))
                 for i, (week, close) in enumerate(weeks))
    return WeeklyHistory(weeks[0][0], datetime.combine(weeks[0][0], datetime.min.time(), NEW_YORK),
                         'security-master-fixture', bars)


def handoff_fixture(open_price='1.11', scheduled=None, factor='1', calendar=None):
    calendar = calendar or Calendar()
    scheduled = scheduled or datetime(2026, 10, 8, 10, 15, tzinfo=NEW_YORK)
    factor = D(factor)
    inputs = EligibilityInputs(multiply(D('.90'), factor), True, 'fixture-basis')
    detector = DDetector('security', 'TEST', calendar, run_id='entry-run', data_version='fixture')
    at = scheduled - timedelta(minutes=27)
    sources = {}

    def feed(low, high, close, volume='100'):
        nonlocal at
        bar = minute(at, low=str(multiply(D(low), factor)), high=str(multiply(D(high), factor)),
                     close=str(multiply(D(close), factor)), volume=volume)
        sources[at] = bar
        detector.feed(bar, inputs)
        at += timedelta(minutes=1)

    for _ in range(20):
        feed('1.001', '1.01', '1.005')
    feed('1', '1.01', '1')
    feed('1', '1.10', '1.10', '200')
    feed('1.06', '1.09', '1.08')
    for _ in range(3):
        feed('1.07', '1.09', '1.08')
    feed('1.07', '1.13', '1.105', '200')
    assert at == scheduled
    actual_open = D(open_price)
    scheduled_bar = minute(at, low=str(min(actual_open, multiply(D('.95'), factor))),
                           high=str(max(actual_open, multiply(D('1.15'), factor))), close=open_price,
                           opening=open_price, volume='400')
    detector.feed(scheduled_bar, inputs)
    handoff = detector.d_state.handoff
    assert handoff is not None
    session = calendar.session_for(scheduled.date())
    supplied = []
    at = session.open
    while at < scheduled:
        bar = sources.get(at) or minute(at, low=str(multiply(D('.99'), factor)),
                                      high=str(multiply(D('1.01'), factor)),
                                      close=str(factor))
        supplied.append(ContextMinute(bar, bar.end, inputs.share_basis_id))
        at += timedelta(minutes=1)
    eligibility = OpeningEligibility(inputs.prior_regular_close, True, session.open,
                                     'eligibility-fixture', inputs.share_basis_id, True)
    context = EntryContext(eligibility, tuple(supplied), weekly_history(scheduled, calendar))
    return handoff, context, calendar, detector
