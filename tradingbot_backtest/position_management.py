"""Single-position research exits; no portfolio counters, allocation or broker."""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from fractions import Fraction

from .audit import AuditRecord, AuditValue
from .codes import ReasonCode, RunStatus
from .market import IntervalClassification as K, MarketInterval
from .numerics import add, multiply, subtract
from .protective_ticks import normalize_protective_stop
from .sessions import SessionCalendar
from .states import StrategyState as S
from .trade_construction import InitialPosition, TickPurpose, TickQuery, TickSource


class ManagementStatus(StrEnum):
    ACTIVE = 'ACTIVE'
    PAUSED_DATA = 'PAUSED_DATA'
    INCOMPLETE_DATA = 'INCOMPLETE_DATA'
    INCOMPLETE_TICK = 'INCOMPLETE_TICK'
    INCOMPLETE_EOD = 'INCOMPLETE_EOD'
    CLOSED = 'CLOSED'


class ExitReason(StrEnum):
    INITIAL_STOP = 'INITIAL_STOP'
    PARTIAL_2R = 'PARTIAL_2R'
    BREAKEVEN_STOP = 'BREAKEVEN_STOP'
    TRAILING_STOP = 'TRAILING_STOP'
    EOD_LIQUIDATION = 'EOD_LIQUIDATION'


class TradeOutcome(StrEnum):
    WIN = 'WIN'
    LOSS = 'LOSS'
    BREAKEVEN = 'BREAKEVEN'


@dataclass(frozen=True, slots=True)
class ExitFill:
    reason: ExitReason
    quantity: int
    reference_price: Decimal
    simulated_fill_price: Decimal
    interval_start: datetime
    interval_end: datetime
    modeled_event_at: datetime | None
    available_at: datetime
    gross_proceeds: Decimal
    gross_pnl: Decimal
    commission: Decimal
    fees: Decimal
    slippage: Decimal
    net_pnl: Decimal
    full_fill: bool
    gap: bool


@dataclass(frozen=True, slots=True)
class TrailingLow:
    interval_start: datetime
    low: Decimal


@dataclass(frozen=True, slots=True)
class CompletedPosition:
    entry: InitialPosition
    fills: tuple[ExitFill, ...]
    gross_pnl: Decimal
    commissions: Decimal
    fees: Decimal
    slippage: Decimal
    net_pnl: Decimal
    net_return: Fraction
    outcome: TradeOutcome
    final_exit_reason: ExitReason
    final_exit_price: Decimal
    final_exit_modeled_at: datetime | None
    final_exit_available_at: datetime
    exit_interval_start: datetime
    exit_interval_end: datetime
    earliest_fresh_interval_start: datetime

    def permits_fresh_price_candle(self, bar: MarketInterval) -> bool:
        """Quarantine gate only; the owner still checks session/universe eligibility."""
        return (bar.security_id == self.entry.approval.security_id
                and bar.classification == K.TRADED
                and bar.timestamp >= self.earliest_fresh_interval_start)


@dataclass(frozen=True, slots=True)
class PositionState:
    entry: InitialPosition
    phase: S
    status: ManagementStatus
    remaining_quantity: int
    active_stop: Decimal
    active_stop_reason: ExitReason = ExitReason.INITIAL_STOP
    partial_filled: bool = False
    runner_active: bool = False
    breakeven_floor: Decimal | None = None
    partial_interval_start: datetime | None = None
    trailing_candle_count: int = 0
    trailing_window: tuple[TrailingLow, ...] = ()
    fills: tuple[ExitFill, ...] = ()
    gross_realized_pnl: Decimal = Decimal(0)
    commissions: Decimal = Decimal(0)
    fees: Decimal = Decimal(0)
    slippage: Decimal = Decimal(0)
    net_realized_pnl: Decimal = Decimal(0)
    last_trustworthy_price: Decimal | None = None
    last_trustworthy_at: datetime | None = None
    reporting_mark_carried: bool = False
    eod_instruction_active: bool = False
    eod_instruction_at: datetime | None = None
    failure_at: datetime | None = None
    failure_reason: ReasonCode | None = None
    data_quality_reason: str | None = None
    run_status: RunStatus | None = None
    completed: CompletedPosition | None = None

    @property
    def original_quantity(self) -> int:
        return self.entry.quantity

    @property
    def target(self) -> Decimal:
        return self.entry.executable_target

    @property
    def reporting_unrealized_pnl(self) -> Decimal | None:
        if self.last_trustworthy_price is None:
            return None
        return multiply(subtract(self.last_trustworthy_price, self.entry.entry_price), Decimal(self.remaining_quantity))


@dataclass(frozen=True, slots=True)
class ManagementResult:
    state: PositionState
    audit: tuple[AuditRecord, ...]
    exit_fills: tuple[ExitFill, ...]
    requires_shared_pause: bool
    requires_shared_stop: bool


class PositionManager:
    """Chronological OPEN/completion phases, immutable snapshots and exit legs.

    Supply every minute starting with the entry interval. Unknown intervals must
    be explicit MISSING/INVALID records, never silently skipped. feed() is an
    offline convenience; a future portfolio owner uses on_open()/on_close().
    """
    def __init__(self, position: InitialPosition, calendar: SessionCalendar, ticks: TickSource):
        if not isinstance(position, InitialPosition):
            raise TypeError('Only a Phase 6 InitialPosition can be managed')
        if (position.quantity < position.config.min_entry_shares or position.risk_per_share <= 0
                or position.executable_stop <= 0 or position.executable_stop >= position.entry_price
                or position.executable_target <= position.entry_price or not position.full_fill
                or any(x != 0 for x in (position.commission, position.fees, position.slippage))):
            raise ValueError('Supply a valid open ZERO-FRICTION Phase 6 position')
        session = calendar.session_for(position.approval.session.trading_date)
        if session is None or session != position.approval.session:
            raise ValueError('Verified calendar/session must match the entry approval')
        self.session, self.ticks = session, ticks
        self.state = PositionState(position, S.OPEN_POSITION, ManagementStatus.ACTIVE,
                                   position.quantity, position.executable_stop,
                                   last_trustworthy_price=position.entry_price,
                                   last_trustworthy_at=position.entry_timestamp)
        self.next_interval = position.entry_timestamp
        self._pending: MarketInterval | None = None
        self._checkpoint = self.state
        self._records: list[AuditRecord] = []

    @property
    def audit(self) -> tuple[AuditRecord, ...]:
        return tuple(self._records)

    def _result(self, record_start: int, fill_start: int) -> ManagementResult:
        return ManagementResult(self.state, tuple(self._records[record_start:]), self.state.fills[fill_start:],
                                self.state.status == ManagementStatus.PAUSED_DATA,
                                self.state.status in (ManagementStatus.INCOMPLETE_DATA, ManagementStatus.INCOMPLETE_TICK,
                                                      ManagementStatus.INCOMPLETE_EOD))

    def _audit(self, event: str, at: datetime, bar: MarketInterval | None = None, *,
               modeled_at: datetime | None = None, reason: ReasonCode | None = None,
               **details: AuditValue) -> None:
        entry = self.state.entry
        self._records.append(AuditRecord(entry.run_id, entry.data_version, event, at,
            security_id=entry.approval.security_id, ticker=entry.approval.ticker,
            setup_id=entry.approval.setup_id, trade_id=entry.trade_id, modeled_event_at=modeled_at,
            interval_start=bar.timestamp if bar else None, interval_end=bar.end if bar else None,
            state=self.state.phase, reason=reason, status=self.state.run_status, details=tuple(details.items())))

    def _require_active(self) -> None:
        if self.state.status != ManagementStatus.ACTIVE:
            raise ValueError('Position management is paused/incomplete; no later prices may be processed')

    def _pause(self, bar: MarketInterval, cause: str | None = None) -> None:
        reason = ReasonCode.INVALID_DATA if bar.classification == K.INVALID else ReasonCode.DATA_GAP
        self.state = replace(self.state, status=ManagementStatus.PAUSED_DATA,
                             failure_at=bar.timestamp, failure_reason=reason,
                             data_quality_reason=cause or bar.data_quality_reason)
        self._audit('OPEN_POSITION_DATA_PAUSE', bar.timestamp, bar, reason=reason,
                    data_quality_reason=self.state.data_quality_reason, active_stop=self.state.active_stop,
                    remaining_quantity=self.state.remaining_quantity, shared_portfolio_pause_required=True)

    def _protective(self, raw: Decimal, purpose: TickPurpose, bar: MarketInterval, *,
                    at: datetime, intrabar: bool = False) -> Decimal | None:
        query_at = bar.timestamp if intrabar else at
        query = TickQuery(self.state.entry.approval.security_id, self.state.entry.approval.ticker,
                          self.state.entry.approval.eligibility.share_basis_id, query_at, purpose, raw)
        level = normalize_protective_stop(query, self.ticks, required_until=bar.end if intrabar else None)
        for evidence in level.evidence:
            details = dict(evidence.details)
            if 'purpose' in details:
                details['supplied_purpose'] = details['purpose']
            details.update(requested_purpose=purpose.value, raw=raw, query_timestamp=query_at,
                           required_until=bar.end if intrabar else None)
            self._audit(evidence.event, at, bar, **details)
        if level.price is None:
            if intrabar:
                self.state = replace(self.state, last_trustworthy_price=bar.close,
                                     last_trustworthy_at=bar.end, reporting_mark_carried=False)
            self.state = replace(self.state, status=ManagementStatus.INCOMPLETE_TICK,
                run_status=RunStatus.INCOMPLETE_TICK_SIZE_UNAVAILABLE_OPEN_POSITION,
                failure_at=at, data_quality_reason=level.failure)
            self._audit('OPEN_POSITION_TICK_FAILURE', at, bar, active_stop=self.state.active_stop,
                        remaining_quantity=self.state.remaining_quantity, required_level=raw,
                        purpose=purpose.value, cause=level.failure, shared_run_stop_required=True)
        return level.price

    def _fill(self, bar: MarketInterval, price: Decimal, quantity: int, reason: ExitReason, *,
              opening: bool, gap: bool = False) -> None:
        available = bar.timestamp if opening else bar.end
        modeled = bar.timestamp if opening else None
        gross = multiply(subtract(price, self.state.entry.entry_price), Decimal(quantity))
        proceeds = multiply(price, Decimal(quantity))
        fill = ExitFill(reason, quantity, price, price, bar.timestamp, bar.end, modeled, available,
                        proceeds, gross, Decimal(0), Decimal(0), Decimal(0), gross, True, gap)
        self.state = replace(self.state, remaining_quantity=self.state.remaining_quantity-quantity,
                             fills=self.state.fills+(fill,),
                             gross_realized_pnl=add(self.state.gross_realized_pnl, gross),
                             net_realized_pnl=add(self.state.net_realized_pnl, gross))
        self._audit('SIMULATED_EXIT_FILL', available, bar, modeled_at=modeled,
                    side='SELL', exit_reason=reason.value, requested_quantity=quantity, filled_quantity=quantity,
                    reference_price=price, simulated_fill_price=price, gross_proceeds=proceeds,
                    gross_pnl=gross, net_pnl=gross, commission=Decimal(0), fees=Decimal(0), slippage=Decimal(0),
                    gap=gap, full_fill=True, remaining_quantity=self.state.remaining_quantity,
                    intrabar_time_unknown=not opening, accounting_available_at=available,
                    execution_model='RESEARCH BACKTEST MODEL — V1.0', costs='ZERO-FRICTION BASELINE',
                    fill_assumption='FULL-FILL RESEARCH ASSUMPTION — MARKET DEPTH AND PARTIAL FILLS NOT MODELED')
        if not self.state.remaining_quantity:
            outcome = (TradeOutcome.WIN if self.state.net_realized_pnl > 0 else
                       TradeOutcome.LOSS if self.state.net_realized_pnl < 0 else TradeOutcome.BREAKEVEN)
            completed = CompletedPosition(self.state.entry, self.state.fills, self.state.gross_realized_pnl,
                self.state.commissions, self.state.fees, self.state.slippage, self.state.net_realized_pnl,
                Fraction(self.state.net_realized_pnl) / Fraction(self.state.entry.position_value), outcome,
                reason, price, modeled, available, bar.timestamp, bar.end, bar.end)
            self.state = replace(self.state, phase=S.CLOSED, status=ManagementStatus.CLOSED, completed=completed)
            self._audit('POSITION_CLOSED', available, bar, modeled_at=modeled,
                        final_exit_reason=reason.value, final_exit_price=price, net_pnl=completed.net_pnl,
                        gross_pnl=completed.gross_pnl, classification=outcome.value,
                        exit_containing_interval=bar.timestamp, earliest_fresh_interval=bar.end,
                        completed_trade_delta=1, ending_portfolio_equity=None)

    def _stop_reason(self) -> ExitReason:
        return self.state.active_stop_reason

    def _partial(self, bar: MarketInterval, price: Decimal, *, opening: bool) -> None:
        at = bar.timestamp if opening else bar.end
        self._fill(bar, price, self.state.original_quantity // 2, ExitReason.PARTIAL_2R,
                   opening=opening, gap=opening and price > self.state.target)
        self.state = replace(self.state, phase=S.PARTIAL_2R, partial_filled=True, runner_active=True,
                             partial_interval_start=bar.timestamp)
        self._audit('PARTIAL_2R', at, bar, modeled_at=bar.timestamp if opening else None,
                    original_quantity=self.state.original_quantity, runner_quantity=self.state.remaining_quantity,
                    partial_quantity=self.state.original_quantity//2, target_complete=True)
        floor = self._protective(self.state.entry.entry_price, TickPurpose.BREAKEVEN_STOP, bar,
                                 at=at, intrabar=not opening)
        if floor is None:
            return
        old = self.state.active_stop
        self.state = replace(self.state, phase=S.RUNNER, breakeven_floor=floor, active_stop=max(old, floor),
                             active_stop_reason=ExitReason.BREAKEVEN_STOP if floor >= old else self.state.active_stop_reason)
        self._audit('RUNNER_BREAKEVEN_ACTIVATED', at, bar,
                    entry_reference=self.state.entry.entry_price, executable_breakeven=floor,
                    stop_before=old, stop_after=self.state.active_stop, same_bar_rule_applies=True)

    def on_open(self, bar: MarketInterval) -> ManagementResult:
        """Use only opening information; never consult later HIGH/LOW/CLOSE/volume."""
        start, fills = len(self._records), len(self.state.fills)
        if self.state.status == ManagementStatus.CLOSED:
            return self._result(start, fills)
        self._require_active()
        if self._pending is not None:
            raise ValueError('Complete the pending interval before another OPEN')
        if (bar.security_id != self.state.entry.approval.security_id or bar.ticker != self.state.entry.approval.ticker
                or bar.timestamp != self.next_interval or not self.session.open <= bar.timestamp < self.session.close):
            raise ValueError('Supply matching, contiguous eligible session intervals; do not bridge gaps')
        if (bar.timestamp == self.state.entry.entry_timestamp and bar.classification not in (K.MISSING, K.INVALID)
                and (bar.classification != K.TRADED or bar.open != self.state.entry.entry_price)):
            raise ValueError('Entry-interval data must preserve the Phase 6 approved opening fill')
        self._checkpoint, self._pending = self.state, bar
        liquidation = self.session.close-self.state.entry.config.forced_liquidation_before_session_close
        if bar.timestamp == liquidation and not self.state.eod_instruction_active:
            self.state = replace(self.state, eod_instruction_active=True, eod_instruction_at=bar.timestamp)
            self._audit('EOD_LIQUIDATION_INSTRUCTION', bar.timestamp, bar, modeled_at=bar.timestamp,
                        irrevocable=True, official_close=self.session.close)
        if bar.classification in (K.MISSING, K.INVALID):
            self._pause(bar)
            return self._result(start, fills)
        self._audit('POSITION_INTERVAL_OPEN', bar.timestamp, bar, modeled_at=bar.timestamp,
                    classification=bar.classification.value, open=bar.open,
                    active_stop=self.state.active_stop, target=self.state.target,
                    target_complete=self.state.partial_filled, remaining_quantity=self.state.remaining_quantity)
        if bar.classification == K.NO_TRADE:
            return self._result(start, fills)
        self.state = replace(self.state, last_trustworthy_price=bar.open, last_trustworthy_at=bar.timestamp,
                             reporting_mark_carried=False)
        if self.state.eod_instruction_active:
            self._fill(bar, bar.open, self.state.remaining_quantity, ExitReason.EOD_LIQUIDATION, opening=True)
        elif bar.open <= self.state.active_stop:
            self._fill(bar, bar.open, self.state.remaining_quantity, self._stop_reason(),
                       opening=True, gap=bar.open < self.state.active_stop)
        elif not self.state.partial_filled and bar.open >= self.state.target:
            self._partial(bar, bar.open, opening=True)
        return self._result(start, fills)

    def on_close(self, bar: MarketInterval) -> ManagementResult:
        """Consume completed extrema, then set prospective trailing levels."""
        start, fills = len(self._records), len(self.state.fills)
        if self.state.status == ManagementStatus.CLOSED:
            return self._result(start, fills)
        self._require_active()
        if self._pending != bar:
            raise ValueError('Completion must match the supplied opening interval; corrections require replay')
        if bar.classification == K.NO_TRADE:
            self.state = replace(self.state, reporting_mark_carried=True)
            self._audit('OPEN_POSITION_NO_TRADE', bar.end, bar, volume=Decimal(0),
                        reporting_only_mark=self.state.last_trustworthy_price,
                        remaining_quantity=self.state.remaining_quantity, active_stop=self.state.active_stop,
                        trailing_count=self.state.trailing_candle_count, eod_instruction_active=self.state.eod_instruction_active)
        else:
            stop_hit = bar.low <= self.state.active_stop
            target_hit = not self.state.partial_filled and bar.high >= self.state.target
            self._audit('POSITION_INTERVAL_COMPLETED', bar.end, bar,
                        low=bar.low, high=bar.high, close=bar.close, stop_before=self.state.active_stop,
                        stop_hit=stop_hit, target_hit=target_hit)
            if stop_hit:
                if target_hit:
                    self._audit('STOP_TARGET_AMBIGUITY', bar.end, bar, reason=ReasonCode.INTRABAR_SEQUENCE_AMBIGUITY,
                                conservative_result='ORIGINAL_STOP_FIRST')
                self._fill(bar, self.state.active_stop, self.state.remaining_quantity, self._stop_reason(), opening=False)
            elif target_hit:
                self._partial(bar, self.state.target, opening=False)
                if self.state.status == ManagementStatus.ACTIVE and bar.low <= self.state.active_stop:
                    self._audit('SAME_BAR_2R_BREAKEVEN_AMBIGUITY', bar.end, bar,
                                reason=ReasonCode.SAME_BAR_2R_BREAKEVEN_AMBIGUITY,
                                executable_breakeven=self.state.active_stop)
                    self._fill(bar, self.state.active_stop, self.state.remaining_quantity, self._stop_reason(), opening=False)
            if self.state.status == ManagementStatus.ACTIVE:
                self.state = replace(self.state, last_trustworthy_price=bar.close, last_trustworthy_at=bar.end,
                                     reporting_mark_carried=False)
                if self.state.runner_active and bar.timestamp != self.state.partial_interval_start:
                    self._trail(bar)
        if self.state.status == ManagementStatus.ACTIVE:
            self.next_interval, self._pending = bar.end, None
            if bar.end == self.session.close:
                self.finish_session()
        return self._result(start, fills)

    def _trail(self, bar: MarketInterval) -> None:
        activation = self.state.entry.config.runner_trailing_activation_traded_candles
        length = self.state.entry.config.runner_low_lookback_traded_candles
        window = (self.state.trailing_window+(TrailingLow(bar.timestamp, bar.low),))[-length:]
        self.state = replace(self.state, trailing_candle_count=self.state.trailing_candle_count+1,
                             trailing_window=window)
        self._audit('TRAILING_PROGRESS', bar.end, bar, count=self.state.trailing_candle_count,
                    required_count=activation, lookback_count=length,
                    window_timestamps='|'.join(item.interval_start.isoformat() for item in window),
                    window_lows='|'.join(str(item.low) for item in window))
        if self.state.trailing_candle_count < activation or len(window) < length:
            return
        raw = min(item.low for item in window)
        if raw <= self.state.active_stop:
            self._audit('TRAILING_STOP_UNCHANGED', bar.end, bar, raw_candidate=raw,
                        active_stop=self.state.active_stop, tick_recalculation_required=False)
            return
        normalized = self._protective(raw, TickPurpose.TRAILING_STOP, bar, at=bar.end)
        if normalized is None:
            return
        old = self.state.active_stop
        new = max(old, self.state.breakeven_floor, normalized)
        self.state = replace(self.state, active_stop=new,
                             active_stop_reason=ExitReason.TRAILING_STOP if new > old else self.state.active_stop_reason)
        self._audit('TRAILING_STOP_UPDATED', bar.end, bar, raw_candidate=raw, normalized_candidate=normalized,
                    stop_before=old, stop_after=self.state.active_stop, effective_from=bar.end,
                    applies_to_calculation_candle=False)

    def feed(self, bar: MarketInterval) -> ManagementResult:
        """Offline convenience; results retain distinct opening/completion times."""
        start, fills = len(self._records), len(self.state.fills)
        self.on_open(bar)
        if self.state.status == ManagementStatus.ACTIVE:
            self.on_close(bar)
        return self._result(start, fills)

    def resume_with_replacement(self, bar: MarketInterval) -> ManagementResult:
        """Replay the failed interval only after reliable replacement, never skip."""
        if (self.state.status != ManagementStatus.PAUSED_DATA or self._pending is None
                or bar.timestamp != self._pending.timestamp or bar.classification not in (K.TRADED, K.NO_TRADE)):
            raise ValueError('Supply reliable replacement for the exact paused interval')
        cause, failed_at = self.state.data_quality_reason, self.state.failure_at
        start, fills = len(self._records), len(self._checkpoint.fills)
        self.state, self._pending = self._checkpoint, None
        self._audit('DATA_REPLACEMENT_REPLAY', failed_at, bar, previous_data_quality_reason=cause,
                    replacement_source=bar.source_id, shared_checkpoint_replay_required=True)
        self.feed(bar)
        return self._result(start, fills)

    def mark_data_incomplete(self) -> ManagementResult:
        start, fills = len(self._records), len(self.state.fills)
        if self.state.status != ManagementStatus.PAUSED_DATA:
            raise ValueError('Only unresolved paused data can become data-incomplete')
        self.state = replace(self.state, status=ManagementStatus.INCOMPLETE_DATA)
        self._audit('OPEN_POSITION_DATA_INCOMPLETE', self.state.failure_at, self._pending,
                    data_quality_reason=self.state.data_quality_reason, shared_run_stop_required=True,
                    completed_trade=False, ending_portfolio_equity=None)
        return self._result(start, fills)

    def finish_session(self) -> ManagementResult:
        start, fills = len(self._records), len(self.state.fills)
        if self.state.status != ManagementStatus.ACTIVE:
            return self._result(start, fills)
        if self.next_interval != self.session.close or self._pending is not None:
            raise ValueError('Account for every required interval before official close; do not infer no liquidity')
        self.state = replace(self.state, status=ManagementStatus.INCOMPLETE_EOD,
                             run_status=RunStatus.INCOMPLETE_UNRESOLVED_EOD_LIQUIDITY,
                             failure_reason=ReasonCode.UNRESOLVED_EOD_NO_LIQUIDITY,
                             failure_at=self.session.close)
        self._audit('UNRESOLVED_EOD_NO_LIQUIDITY', self.session.close,
                    reason=ReasonCode.UNRESOLVED_EOD_NO_LIQUIDITY,
                    official_close=self.session.close, eod_instruction_at=self.state.eod_instruction_at,
                    eod_instruction_active=self.state.eod_instruction_active,
                    remaining_quantity=self.state.remaining_quantity, active_stop=self.state.active_stop,
                    reporting_only_mark=self.state.last_trustworthy_price,
                    unrealized_reporting_pnl=self.state.reporting_unrealized_pnl,
                    gross_realized_pnl=self.state.gross_realized_pnl, net_realized_pnl=self.state.net_realized_pnl,
                    shared_run_stop_required=True, completed_trade=False, ending_portfolio_equity=None)
        return self._result(start, fills)
