"""Opening-only Phase 5 candidates. Approval is never an entry fill."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from fractions import Fraction

from .audit import AuditRecord, AuditValue
from .c_detector import CLockEvidence
from .codes import ReasonCode
from .config import FROZEN_V1, StrategyConfig
from .d_detector import BreakoutAttempt, EntryHandoff
from .entry_context import (ContextMinute, IntradayContextResult, WeeklyContextResult,
                            WeeklyHistory, intraday_context, weekly_context)
from .market import IntervalClassification as Kind, MarketInterval, validate_timestamp
from .sessions import NEW_YORK, SessionCalendar, SessionPeriod, TradingSession


@dataclass(frozen=True, slots=True)
class OpeningEligibility:
    prior_regular_close: Decimal | Fraction | None
    static_eligible: bool
    available_at: datetime
    source_id: str
    share_basis_id: str
    normalization_verified: bool
    normalization_effective_at: tuple[datetime, ...] = ()

    def __post_init__(self) -> None:
        validate_timestamp(self.available_at)
        for at in self.normalization_effective_at:
            validate_timestamp(at)
        if type(self.static_eligible) is not bool or type(self.normalization_verified) is not bool:
            raise TypeError('Eligibility and normalization verification must be explicit booleans')
        if not self.source_id or not self.share_basis_id:
            raise ValueError('Supply eligibility source and share-basis identifiers')
        if self.prior_regular_close is not None and (
                type(self.prior_regular_close) not in (Decimal, Fraction)
                or (isinstance(self.prior_regular_close, Decimal) and not self.prior_regular_close.is_finite())
                or self.prior_regular_close <= 0):
            raise ValueError('Supply trustworthy positive exact prior close, or None for unavailable')


@dataclass(frozen=True, slots=True)
class TickReference:
    """Opaque verified source metadata for Phase 6; no numeric tick is assumed."""
    reference_id: str
    source_id: str
    available_at: datetime

    def __post_init__(self) -> None:
        validate_timestamp(self.available_at)
        if not self.reference_id or not self.source_id:
            raise ValueError('Supply tick reference provenance')


@dataclass(frozen=True, slots=True)
class EntryContext:
    eligibility: OpeningEligibility
    minutes: tuple[ContextMinute, ...]
    weekly: WeeklyHistory
    tick_reference: TickReference | None = None


class EntryOutcome(StrEnum):
    ENTRY_APPROVED = 'ENTRY_APPROVED'
    ENTRY_CONSUMED = 'ENTRY_CONSUMED'


@dataclass(frozen=True, slots=True)
class EntryApproval:
    """Approved Phase 5 reference OPEN candidate; ticks/account checks still pending.

    No scheduled-minute HIGH/LOW/CLOSE/volume or full EntryHandoff is retained.
    A/B/C/D evidence is earlier completed structure and may contain its own OHLCV.
    """
    setup_id: str
    security_id: str
    ticker: str
    scheduled_timestamp: datetime
    reference_open: Decimal
    opening_source_id: str
    a: MarketInterval
    b_price: Decimal
    b_origin: MarketInterval
    c_price: Decimal
    c_evidence: CLockEvidence
    d: MarketInterval
    d_confirmation_timestamp: datetime
    d_attempt: BreakoutAttempt
    prior_regular_close: Decimal | Fraction
    opening_change: Fraction
    b_extension: Fraction
    price_eligible: bool
    change_eligible: bool
    intraday: IntradayContextResult
    weekly: WeeklyContextResult
    session: TradingSession
    eligibility: OpeningEligibility
    tick_reference: TickReference | None


@dataclass(frozen=True, slots=True)
class EntryValidationResult:
    setup_id: str
    timestamp: datetime
    outcome: EntryOutcome
    approval: EntryApproval | None
    reason: ReasonCode | None
    description: str
    consumed: bool
    audit: tuple[AuditRecord, ...]


class EntryValidator:
    """One validation per setup. The caller owns the resulting disposition.

    A rejected record consumes the opportunity; fresh detection must apply the
    frozen reset rules. An approved record transfers to Phase 6, not OPEN_POSITION.
    Source ingestion time is distinct from the modeled opening decision time.
    """

    def __init__(self, calendar: SessionCalendar, *, run_id: str, data_version: str,
                 config: StrategyConfig = FROZEN_V1):
        self.calendar, self.config = calendar, config
        self.run_id, self.data_version = run_id, data_version
        self._seen: set[tuple[str, str]] = set()

    def evaluate(self, handoff: EntryHandoff, context: EntryContext) -> EntryValidationResult:
        signal, interval = handoff.signal, handoff.interval
        opening = handoff.modeled_open_timestamp
        key = signal.security_id, signal.setup_id
        if key in self._seen:
            raise ValueError('The setup already used its one scheduled entry validation opportunity')
        self._seen.add(key)
        records: list[AuditRecord] = []

        def audit(event: str, reason: ReasonCode | None = None, **details: AuditValue) -> None:
            records.append(AuditRecord(self.run_id, self.data_version, event, opening,
                                       security_id=signal.security_id, ticker=signal.ticker,
                                       setup_id=signal.setup_id, modeled_event_at=opening,
                                       interval_start=opening, interval_end=interval.end,
                                       reason=reason, details=tuple(details.items())))

        def reject(description: str, reason: ReasonCode | None = None) -> EntryValidationResult:
            audit('ENTRY_CONSUMED', reason, description=description, consumed=True, executed=False)
            return EntryValidationResult(signal.setup_id, opening, EntryOutcome.ENTRY_CONSUMED,
                                         None, reason, description, True, tuple(records))

        # This boundary deliberately reads no scheduled-minute H/L/C/volume.
        audit('OPENING_REFERENCE', scheduled_interval=signal.scheduled_interval,
              open=interval.open, opening_source_id=interval.source_id,
              d_confirmation=signal.d_confirmation_timestamp)
        if (interval.classification != Kind.TRADED or interval.timestamp != opening
                or signal.scheduled_interval != opening or signal.d.end != opening
                or signal.d_confirmation_timestamp != opening
                or interval.security_id != signal.security_id or interval.ticker != signal.ticker
                or signal.a.end > opening or signal.b_origin.end > opening
                or any(bar.end > signal.d.timestamp for bar in signal.c_evidence.development_candles)):
            return reject('INVALID_PHASE4_HANDOFF', ReasonCode.ENTRY_DATA_INVALID)
        day = opening.astimezone(NEW_YORK).date()
        session = self.calendar.session_for(day)
        if (session is None or signal.d.timestamp.astimezone(NEW_YORK).date() != day
                or session.period_at(opening, premarket_start=self.config.premarket_volume_history_start_et)
                != SessionPeriod.REGULAR
                or opening >= session.close - self.config.entry_cutoff_before_session_close):
            return reject('ENTRY_TIME_RESTRICTION', ReasonCode.ENTRY_TIME_RESTRICTION)
        audit('ENTRY_SESSION', session_open=session.open, session_close=session.close,
              calendar_source=session.source_id, cutoff=session.close - self.config.entry_cutoff_before_session_close)
        supplied = context.eligibility
        if supplied.available_at > opening:
            # Neither substantive values nor their source identity is assumed
            # independently known from an unavailable eligibility snapshot.
            audit('ENTRY_INPUT_PROVENANCE', available_at=supplied.available_at, status='UNAVAILABLE')
            return reject('OPENING_ELIGIBILITY_DATA_UNAVAILABLE')
        audit('ENTRY_INPUT_PROVENANCE', expected_share_basis=handoff.eligibility_inputs.share_basis_id,
              supplied_share_basis=supplied.share_basis_id, normalization_verified=supplied.normalization_verified,
              normalization_effective_at='|'.join(at.isoformat() for at in supplied.normalization_effective_at),
              source_id=supplied.source_id, available_at=supplied.available_at,
              supplied_prior_close=supplied.prior_regular_close,
              handoff_prior_close=handoff.eligibility_inputs.prior_regular_close)
        if (not supplied.normalization_verified
                or supplied.share_basis_id != handoff.eligibility_inputs.share_basis_id
                or any(at > opening for at in supplied.normalization_effective_at)):
            return reject('Incompatible or unverified point-in-time share units',
                          ReasonCode.CORPORATE_ACTION_DATA_UNAVAILABLE)
        if supplied.prior_regular_close is None:
            return reject('PRIOR_CLOSE_DATA_UNAVAILABLE')
        if supplied.prior_regular_close != handoff.eligibility_inputs.prior_regular_close:
            return reject('PRIOR_CLOSE_REFERENCE_CONFLICT')
        if not supplied.static_eligible:
            return reject('ENTRY_STATIC_ELIGIBILITY_FAILED')
        entry_open, b = Fraction(interval.open), Fraction(signal.b_price)
        extension = (entry_open - b) / b
        above_b = entry_open > b
        below_max = extension <= self.config.entry_max_open_extension_above_b
        audit('ENTRY_B_BOUNDARY', b=signal.b_price, open=interval.open,
              extension=str(extension), strictly_above_b=above_b, within_extension=below_max,
              maximum_extension=str(self.config.entry_max_open_extension_above_b))
        if not above_b:
            return reject('OPEN_AT_OR_BELOW_B', ReasonCode.ENTRY_OPEN_AT_OR_BELOW_B)
        if not below_max:
            return reject('OPEN_ABOVE_MAX_EXTENSION', ReasonCode.ENTRY_OPEN_ABOVE_MAX)
        change = (entry_open - Fraction(supplied.prior_regular_close)) / Fraction(supplied.prior_regular_close)
        price_passed = interval.open <= self.config.max_price_usd
        change_passed = change >= self.config.min_current_day_change
        audit('ENTRY_DYNAMIC_ELIGIBILITY', open=interval.open, prior_close=supplied.prior_regular_close,
              price_passed=price_passed, change_passed=change_passed, change=str(change),
              max_price=self.config.max_price_usd, minimum_change=str(self.config.min_current_day_change),
              eligibility_source=supplied.source_id, available_at=supplied.available_at,
              share_basis=supplied.share_basis_id)
        if not price_passed or not change_passed:
            reason = (ReasonCode.ENTRY_DYNAMIC_MULTIPLE_ELIGIBILITY_FAILURES if not price_passed and not change_passed
                      else ReasonCode.ENTRY_DYNAMIC_PRICE_ABOVE_MAX if not price_passed
                      else ReasonCode.ENTRY_DYNAMIC_DAILY_CHANGE_BELOW_MIN)
            return reject('OPENING_DYNAMIC_ELIGIBILITY_FAILED', reason)
        intraday = intraday_context(context.minutes, session, opening, signal.security_id,
                                   supplied.share_basis_id, self.config)
        for block in intraday.blocks:
            audit('ENTRY_15M_BLOCK', block_start=block.start, block_end=block.end,
                  classification=block.classification, open=block.open, high=block.high, low=block.low,
                  close=block.close, volume=str(block.volume) if block.volume is not None else None,
                  constituent_count=len(block.constituents), failures='|'.join(block.failures),
                  traded_minutes=sum(item.interval.classification == Kind.TRADED for item in block.constituents),
                  no_trade_minutes=sum(item.interval.classification == Kind.NO_TRADE for item in block.constituents))
            for item in block.constituents:
                bar = item.interval
                known = bar.end <= opening and item.available_at <= opening
                audit('ENTRY_15M_CONSTITUENT', timestamp=bar.timestamp, classification=bar.classification.value,
                      source_id=bar.source_id, available_at=item.available_at,
                      high=bar.high if known else None, low=bar.low if known else None,
                      open=bar.open if known else None, close=bar.close if known else None,
                      volume=bar.volume if known else None, share_basis=item.share_basis_id,
                      data_quality_reason=bar.data_quality_reason)
        audit('ENTRY_15M_COMPARISON', intraday.reason, high_passed=intraday.high_passed,
              low_passed=intraday.low_passed)
        if intraday.reason is not None:
            return reject('MANDATORY_15M_CONTEXT_FAILED', intraday.reason)
        weekly = weekly_context(context.weekly, self.calendar, opening, interval.open,
                                signal.security_id, supplied.share_basis_id, self.config)
        for bar in weekly.bars:
            audit('ENTRY_WEEKLY_BAR', week_start=bar.week_start.isoformat(), source_id=bar.source_id,
                  completed_at=bar.completed_at, available_at=bar.available_at,
                  high=bar.high if bar.available_at <= opening else None,
                  open=bar.open if bar.available_at <= opening else None,
                  low=bar.low if bar.available_at <= opening else None,
                  close=bar.close if bar.available_at <= opening else None,
                  share_basis=bar.share_basis_id, classification=bar.classification.value,
                  data_quality_reason=bar.data_quality_reason)
        for item in weekly.evaluations:
            audit('ENTRY_WEEKLY_CANDIDATE', week_start=item.candidate_week.isoformat(), high=item.high,
                  status=item.status, left='|'.join(at.isoformat() for at in item.left_weeks),
                  right='|'.join(at.isoformat() for at in item.right_weeks))
        audit('ENTRY_WEEKLY_RESULT', weekly.reason,
              candidate_weeks='|'.join(at.isoformat() for at in weekly.candidate_weeks),
              left_context_weeks='|'.join(at.isoformat() for at in weekly.context_weeks),
              nearest_resistance=weekly.nearest_resistance,
              room_pct=str(weekly.room_pct) if weekly.room_pct is not None else None,
              minimum_room_pct=str(self.config.weekly_min_room_pct), status=weekly.status,
              failures='|'.join(weekly.failures))
        if weekly.reason is not None:
            return reject('MANDATORY_WEEKLY_CONTEXT_FAILED', weekly.reason)
        tick = context.tick_reference
        if tick is not None and tick.available_at > opening:
            tick = None
        approval = EntryApproval(signal.setup_id, signal.security_id, signal.ticker, opening,
                                 interval.open, interval.source_id, signal.a, signal.b_price, signal.b_origin,
                                 signal.c_price, signal.c_evidence, signal.d, signal.d_confirmation_timestamp,
                                 signal.attempt,
                                 supplied.prior_regular_close, change, extension, price_passed, change_passed,
                                 intraday, weekly, session, supplied, tick)
        audit('ENTRY_APPROVED', reference_open=interval.open, executed=False, consumed=False,
              tick_reference=tick.reference_id if tick else None,
              tick_and_portfolio_validation_deferred=True)
        return EntryValidationResult(signal.setup_id, opening, EntryOutcome.ENTRY_APPROVED,
                                     approval, None, 'PHASE5_CHECKS_PASSED', False, tuple(records))
