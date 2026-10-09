"""Hand-specified offline history. Calendar/ticks are fixture facts, not defaults."""

from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal as D

from tradingbot_backtest.entry_context import WeeklyBar, WeeklyClassification as W
from tradingbot_backtest.historical_data import (Adjustment, DailySession, HistoricalMinute,
    HistoricalWeekly, InMemoryDataset, MarketCapitalization, PriorClose, Provenance,
    SecurityReference, SecurityType, SessionBasis)
from tradingbot_backtest.market import IntervalClassification as K, MarketInterval
from tradingbot_backtest.sessions import NEW_YORK, TradingSession
from tradingbot_backtest.trade_construction import TickRule

DAY = date(2026,10,8)


class Calendar:
    def __init__(self, holidays=(), early=()):
        self.holidays, self.early = frozenset(holidays), frozenset(early)

    def session_for(self, day):
        if day.weekday() >= 5 or day in self.holidays:
            return None
        return TradingSession(day,datetime(day.year,day.month,day.day,9,30,tzinfo=NEW_YORK),
            datetime(day.year,day.month,day.day,13 if day in self.early else 16,0,tzinfo=NEW_YORK),
            'fixture-verified-sessions')


class Ticks:
    def __init__(self, fail_purposes=()):
        self.fail_purposes=frozenset(fail_purposes)

    def resolve(self, query):
        if query.purpose in self.fail_purposes:
            return None
        return TickRule(query.security_id,'basis',query.purpose,D('.01'),D(0),D(0),None,
            datetime(2020,1,1,tzinfo=NEW_YORK),None,datetime(2020,1,1,tzinfo=NEW_YORK),
            'fixture-verified-ticks','fixture uniform grid',True)


def provenance(at, basis='basis', verified=True, adjustment=Adjustment.RAW, cause=None):
    return Provenance('independent-source',at,basis,adjustment,verified,cause)


def minute(security,ticker,at,opening='1.005',high='1.01',low='1.001',close='1.005',
           volume='100',kind=K.TRADED,cause='source gap'):
    fields=dict(security_id=security,ticker=ticker,timestamp=at,classification=kind,source_id='minute-source')
    if kind==K.TRADED:
        fields.update(open=D(opening),high=D(high),low=D(low),close=D(close),volume=D(volume))
    elif kind==K.NO_TRADE:
        fields.update(volume=D(0),no_trade_verified=True)
    else:
        fields.update(data_quality_reason=cause)
    bar=MarketInterval(**fields)
    return HistoricalMinute(bar,at,bar.end,provenance(at))


def dataset(symbols=(('id-AAA','AAA'),), days=(DAY,), *, calendar=None, ticks=None,
            adv='2000000', adr_high='1.10', adr_low='1.00', adr_close='1.00'):
    calendar=calendar or Calendar()
    refs=[]; bases=[]; daily=[]; closes=[]; caps=[]; minutes=[]; weekly=[]
    for security,ticker in symbols:
        listing=date(2026,1,5)
        known=datetime(2026,1,1,tzinfo=NEW_YORK)
        refs.append(SecurityReference(security,ticker,known,None,known,listing,known,None,
            'verified-listed-venue',SecurityType.COMMON_STOCK,True,True,False,True,'security-master'))
        # Explicit fixture daily records cover all expected sessions in the range.
        history_day=date(2026,8,1)
        while history_day < max(days):
            session=calendar.session_for(history_day)
            if session:
                daily.append(DailySession(security,history_day,session.close,provenance(session.close),
                    D(adr_high),D(adr_low),D(adr_close),D(adv),True))
            history_day+=timedelta(days=1)
        for day in days:
            session=calendar.session_for(day)
            if session is None:
                continue
            bases.append(SessionBasis(security,day,'basis',session.open,session.open,'action-source',True))
            prior=day-timedelta(days=1)
            while calendar.session_for(prior) is None:
                prior-=timedelta(days=1)
            closes.append(PriorClose(security,prior,D('.90'),provenance(calendar.session_for(prior).close),True))
            caps.append(MarketCapitalization(security,session.open,session.close,session.open,
                'historical-market-cap',True,D('50000000')))
            at=datetime(day.year,day.month,day.day,4,0,tzinfo=NEW_YORK)
            while at < session.close:
                minutes.append(minute(security,ticker,at,
                    low='.99' if session.open <= at < session.open+timedelta(minutes=15) else '1.001'))
                at+=timedelta(minutes=1)
        current=min(days)-timedelta(days=min(days).weekday())
        week=listing
        while week < max(days):
            sessions=[calendar.session_for(week+timedelta(days=i)) for i in range(5)]
            sessions=[s for s in sessions if s]
            if sessions:
                end=max(s.close for s in sessions)
                weekly.append(HistoricalWeekly(WeeklyBar(security,week,end,end,'weekly-source','basis',W.VALID,D('.80')),
                    provenance(end)))
            week+=timedelta(days=7)
    return InMemoryDataset('synthetic-dataset','fixture-v1',calendar,ticks or Ticks(),'calendar-v1','ticks-v1',
        tuple(refs),tuple(bases),tuple(daily),tuple(closes),tuple(caps),(),tuple(minutes),tuple(weekly))


def with_setup(data, security='id-AAA', ticker='AAA', scheduled=None, outcome='win', boost='600'):
    """A=1, B=1.10, C=1.06, Dclose=1.105, entry=1.11 (1000 shares)."""
    scheduled=scheduled or datetime(2026,10,8,10,15,tzinfo=NEW_YORK)
    start=scheduled-timedelta(minutes=7)
    replacements={}
    values=(('1','1.01','1','1','100'),
            ('1.10','1.10','1','1.10',boost),
            ('1.08','1.09','1.06','1.08','100'),
            ('1.08','1.09','1.07','1.08','100'),
            ('1.08','1.09','1.07','1.08','100'),
            ('1.08','1.09','1.07','1.08','100'),
            ('1.105','1.13','1.07','1.105',boost))
    for i,(o,h,l,c,v) in enumerate(values):
        replacements[start+timedelta(minutes=i)]=minute(security,ticker,start+timedelta(minutes=i),o,h,l,c,v)
    replacements[scheduled]=minute(security,ticker,scheduled,'1.11','1.12','1.105','1.11')
    session=data.calendar.session_for(scheduled.date())
    at=scheduled+timedelta(minutes=1)
    while at < session.close:
        replacements[at]=minute(security,ticker,at,'1.11','1.14','1.105','1.11')
        at+=timedelta(minutes=1)
    if outcome=='win':
        at=scheduled+timedelta(minutes=1)
        replacements[at]=minute(security,ticker,at,'1.23','1.24','1.23','1.23')
        at+=timedelta(minutes=1)
        replacements[at]=minute(security,ticker,at,'1.11','1.12','1.11','1.11')
    elif outcome=='loss':
        at=scheduled+timedelta(minutes=1)
        replacements[at]=minute(security,ticker,at,'1.05','1.05','1.04','1.05')
    elif outcome=='big_loss':
        at=scheduled+timedelta(minutes=1)
        replacements[at]=minute(security,ticker,at,'1.00','1.02','.99','1.00')
    return replace(data,minute_records=tuple(replacements.get(r.interval.timestamp,r)
        if r.interval.security_id==security else r for r in data.minute_records))


def replace_minute(data, security, at, **changes):
    records=[]
    for record in data.minute_records:
        if record.interval.security_id==security and record.interval.timestamp==at:
            bar=replace(record.interval,**changes)
            record=replace(record,interval=bar)
        records.append(record)
    return replace(data,minute_records=tuple(records))
