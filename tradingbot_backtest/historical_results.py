"""Exact research ledgers, sampled confirmed-equity metrics and fingerprints."""

from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from enum import Enum
from fractions import Fraction
from hashlib import sha256
import json

from .config import StrategyConfig
from .historical_data import HistoricalDataset
from .numerics import multiply, subtract
from .portfolio import PortfolioState
from .position_management import CompletedPosition, PositionState


def canonical(value: object) -> object:
    if isinstance(value, Enum): return value.value
    if isinstance(value, datetime): return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date): return value.isoformat()
    if isinstance(value, time): return value.isoformat()
    if isinstance(value, timedelta):
        return {'days': value.days, 'seconds': value.seconds, 'microseconds': value.microseconds}
    if isinstance(value, Decimal): return {'decimal':str(value)}
    if isinstance(value, Fraction): return {'numerator':value.numerator,'denominator':value.denominator}
    if is_dataclass(value): return {f.name:canonical(getattr(value,f.name)) for f in fields(value)}
    if isinstance(value, dict): return {str(k):canonical(v) for k,v in sorted(value.items(),key=lambda p:str(p[0]))}
    if isinstance(value, (tuple,list)):
        return [canonical(v) for v in value]
    if value is None or type(value) in (str,int,bool): return value
    raise TypeError('Only explicit public deterministic metadata may enter results')


def serialized(value: object) -> str:
    return json.dumps(canonical(value),sort_keys=True,separators=(',',':'),ensure_ascii=False)


@dataclass(frozen=True, slots=True)
class ReproducibilityManifest:
    strategy_version: str
    config_sha256: str
    dataset_id: str
    source_version: str
    dataset_sha256: str
    contract_version: str
    calendar_version: str
    tick_version: str
    start: date
    end: date
    security_ids: tuple[str, ...]
    code_version: str | None


def manifest(dataset: HistoricalDataset, start: date, end: date,
             config: StrategyConfig, code_version: str | None) -> ReproducibilityManifest:
    refs = dataset.security_references()
    ids = tuple(sorted({r.security_id for r in refs}))
    def ordered(records):
        return sorted((canonical(record) for record in records),key=lambda r:json.dumps(r,sort_keys=True))
    raw = dict(references=ordered(refs),daily=[],closes=[],caps=[],actions=[],bases=[],minutes=[],weekly=[],sessions=[])
    for security in ids:
        for key,records in [('daily',dataset.daily_sessions(security)),('closes',dataset.prior_closes(security)),
                            ('caps',dataset.market_caps(security)),('actions',dataset.split_actions(security))]:
            raw[key].extend(records)
        day = start
        from datetime import timedelta
        while day <= end:
            raw['bases'].append(dataset.session_basis(security,day))
            raw['minutes'].extend(dataset.minutes(security,day))
            raw['weekly'].extend(dataset.weekly_history(security,day))
            day += timedelta(days=1)
    for key in ('daily','closes','caps','actions','bases','minutes','weekly'):
        raw[key] = ordered(raw[key])
    from datetime import timedelta
    # Fingerprint actual calendar facts used in history and execution, including
    # verified non-session dates. TickSource is addressed by its supplied immutable
    # tick_version; opaque callbacks must not be introspected for credentials.
    earliest = min([start]+[r.listing_date for r in refs])
    calendar_start = max(earliest,start-timedelta(weeks=config.weekly_max_candidate_weeks+config.weekly_swing_left_bars+1))
    calendar_day = calendar_start
    while calendar_day <= end:
        raw['sessions'].append((calendar_day,dataset.calendar.session_for(calendar_day)))
        calendar_day += timedelta(days=1)
    raw['sessions'] = ordered(raw['sessions'])
    raw.update(dataset_id=dataset.dataset_id,source_version=dataset.source_version,
               calendar_version=dataset.calendar_version,tick_version=dataset.tick_version)
    return ReproducibilityManifest('1.0',sha256(serialized(config).encode()).hexdigest(),
        dataset.dataset_id,dataset.source_version,sha256(serialized(raw).encode()).hexdigest(),
        'historical-contract-1',dataset.calendar_version,dataset.tick_version,start,end,ids,code_version)


@dataclass(frozen=True, slots=True)
class TradeLedger:
    completed: CompletedPosition
    realized_r: Fraction
    initial_fixed_risk: Decimal
    session_lockout_context: tuple[tuple[str, str | int], ...]

    @property
    def trade_id(self): return self.completed.entry.trade_id

    @property
    def setup_id(self): return self.completed.entry.approval.setup_id

    @property
    def security_id(self): return self.completed.entry.approval.security_id

    @property
    def ticker(self): return self.completed.entry.approval.ticker

    @property
    def trading_date(self): return self.completed.entry.approval.session.trading_date


@dataclass(frozen=True, slots=True)
class EquityPoint:
    timestamp: datetime
    equity: Decimal
    phase: str


@dataclass(frozen=True, slots=True)
class SessionSummary:
    state: PortfolioState
    wins: int
    losses: int
    breakevens: int
    daily_return: Fraction | None
    maximum_positions: int


@dataclass(frozen=True, slots=True)
class BacktestSummary:
    starting_equity: Decimal
    ending_confirmed_equity: Decimal | None
    total_net_pnl: Decimal | None
    total_return: Fraction | None
    sessions: int
    entries: int
    completed_trades: int
    wins: int
    losses: int
    breakevens: int
    win_rate: Fraction | None
    average_win: Fraction | None
    average_loss: Fraction | None
    profit_factor: Fraction | None
    average_trade_net: Fraction | None
    largest_win: Decimal | None
    largest_loss: Decimal | None
    maximum_drawdown_confirmed_path: Fraction
    lockout_counts: tuple[tuple[str,int], ...]
    incomplete: bool
    status: str


@dataclass(frozen=True, slots=True)
class BacktestResult:
    manifest: ReproducibilityManifest
    trades: tuple[TradeLedger, ...]
    sessions: tuple[SessionSummary, ...]
    summary: BacktestSummary
    equity_chronology: tuple[EquityPoint, ...]
    unresolved_positions: tuple[PositionState, ...]
    universe_evaluations: tuple[object, ...]
    audit: tuple[object, ...]
    last_confirmed_equity: Decimal
    last_processed_at: datetime | None
    incomplete_reason: str | None
    disclosures: tuple[str, ...]

    def to_json(self) -> str:
        return serialized(self)


def summarize(sessions: tuple[SessionSummary,...], trades: tuple[TradeLedger,...],
              equity: tuple[EquityPoint,...], config: StrategyConfig,
              incomplete: bool) -> BacktestSummary:
    values = [t.completed.net_pnl for t in trades]
    wins,losses = [v for v in values if v>0],[v for v in values if v<0]
    gains = sum(map(Fraction,wins),Fraction()); negatives = sum(map(Fraction,losses),Fraction())
    ending = sessions[-1].state.ending_equity if sessions and not incomplete else (config.starting_equity_usd if not incomplete else None)
    pnl = subtract(ending,config.starting_equity_usd) if ending is not None else None
    peak, drawdown = Fraction(config.starting_equity_usd),Fraction()
    for point in equity:
        value=Fraction(point.equity); peak=max(peak,value)
        if peak>0: drawdown=max(drawdown,(peak-value)/peak)
    counts={}
    for session in sessions:
        for reason in session.state.lockout_reasons: counts[reason.value]=counts.get(reason.value,0)+1
    return BacktestSummary(config.starting_equity_usd,ending,pnl,
        Fraction(pnl)/Fraction(config.starting_equity_usd) if pnl is not None else None,
        len(sessions),sum(s.state.new_entries for s in sessions),len(trades),len(wins),len(losses),
        sum(v==0 for v in values),Fraction(len(wins),len(values)) if values else None,
        gains/len(wins) if wins else None,negatives/len(losses) if losses else None,
        gains/-negatives if negatives<0 else None,sum(map(Fraction,values),Fraction())/len(values) if values else None,
        max(wins) if wins else None,min(losses) if losses else None,drawdown,
        tuple(sorted(counts.items())),incomplete,'BACKTEST INCOMPLETE' if incomplete else 'BACKTEST COMPLETE')
