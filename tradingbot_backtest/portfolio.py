"""Shared, offline session orchestration; no scanner, dataset runner or broker."""

from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from fractions import Fraction

from .audit import AuditRecord, AuditValue
from .codes import ReasonCode, RunStatus
from .config import FROZEN_V1, StrategyConfig
from .entry_validation import EntryApproval
from .market import IntervalClassification as K, MarketInterval, validate_timestamp
from .numerics import add, decimal, multiply, subtract
from .position_management import CompletedPosition, ManagementResult, PositionManager, PositionState
from .sessions import SessionCalendar, TradingSession
from .trade_construction import HeldPosition, PortfolioSnapshot, TickSource, TradeConstructionResult, TradeConstructor


class PortfolioStatus(StrEnum):
    ACTIVE = 'ACTIVE'
    PAUSED_DATA = 'PAUSED_DATA'
    INCOMPLETE = 'INCOMPLETE'
    SESSION_CLOSED = 'SESSION_CLOSED'


class LockoutReason(StrEnum):
    DAILY_ENTRY_LIMIT = 'DAILY_ENTRY_LIMIT'
    DAILY_NET_REALIZED_LOSS = 'DAILY_NET_REALIZED_LOSS'
    CONSECUTIVE_NET_LOSSES = 'CONSECUTIVE_NET_LOSSES'


@dataclass(frozen=True, slots=True)
class PortfolioState:
    session: TradingSession
    bod_equity: Decimal
    cash: Decimal
    current_equity: Decimal | None
    exposure: Decimal | None
    reporting_equity: Decimal | None
    reporting_exposure: Decimal | None
    positions: tuple[PositionState, ...] = ()
    new_entries: int = 0
    completed_trades: int = 0
    realized_gross: Decimal = Decimal(0)
    commissions: Decimal = Decimal(0)
    fees: Decimal = Decimal(0)
    slippage: Decimal = Decimal(0)
    realized_net: Decimal = Decimal(0)
    consecutive_losses: int = 0
    lockout_reasons: tuple[LockoutReason, ...] = ()
    status: PortfolioStatus = PortfolioStatus.ACTIVE
    run_status: RunStatus | None = None
    last_event_at: datetime | None = None
    ending_equity: Decimal | None = None

    @property
    def trading_date(self) -> date:
        return self.session.trading_date

    @property
    def open_position_count(self) -> int:
        return len(self.positions)

    @property
    def locked(self) -> bool:
        return bool(self.lockout_reasons)

    @property
    def realized_costs(self) -> Decimal:
        # Slippage is already in fill-based gross P&L, never subtract it again.
        return add(self.commissions, self.fees)


@dataclass(frozen=True, slots=True)
class CandidateDecision:
    approval: EntryApproval
    rank: int
    accepted: bool
    description: str
    reason: ReasonCode | None
    consumed: bool
    construction: TradeConstructionResult | None
    cash_before: Decimal
    cash_after: Decimal
    exposure_before: Decimal | None
    exposure_after: Decimal | None


@dataclass(frozen=True, slots=True)
class PortfolioStep:
    state: PortfolioState
    candidates: tuple[CandidateDecision, ...]
    audit: tuple[AuditRecord, ...]
    requires_shared_pause: bool
    requires_shared_stop: bool


def rank_candidates(approvals: tuple[EntryApproval, ...]) -> tuple[EntryApproval, ...]:
    """Spec §15 / Decision #12: finite D RVOL descending, alphabetical ticker."""
    securities, tickers = set(), set()
    for approval in approvals:
        if not isinstance(approval, EntryApproval):
            raise TypeError('Supply Phase 5 EntryApproval records only')
        ratio = approval.d_attempt.previous_20.ratio
        if not isinstance(ratio, Fraction) or ratio <= 0 or not approval.d_attempt.rvol_passed:
            raise ValueError('Approved candidates require finite valid D RVOL')
        if approval.security_id in securities or approval.ticker in tickers:
            raise ValueError('Overlapping/duplicate candidates violate one setup per ticker')
        securities.add(approval.security_id)
        tickers.add(approval.ticker)
    return tuple(sorted(approvals, key=lambda a: (-a.d_attempt.previous_20.ratio, a.ticker)))


class PortfolioEngine:
    """Coordinate supplied Phase 5 approvals with unchanged Phase 6/7 engines.

    Signal producers own Phase 1–5 and supply opening-only approvals. They must
    consult price_pattern_permitted() before letting occupied/quarantined prices
    participate, and separately rebuild fresh detection/session price history.
    Every held minute is explicit. OPEN and completion are separate API calls.
    """
    def __init__(self, calendar: SessionCalendar, ticks: TickSource, *, run_id: str,
                 data_version: str, config: StrategyConfig = FROZEN_V1):
        self.calendar, self.ticks, self.config = calendar, ticks, config
        self.run_id, self.data_version = run_id, data_version
        self.constructor = TradeConstructor(run_id=run_id, data_version=data_version, config=config)
        self.state: PortfolioState | None = None
        self.completed: tuple[CompletedPosition, ...] = ()
        self._managers: dict[str, PositionManager] = {}
        self._quarantines: dict[str, CompletedPosition] = {}
        self._consumed: set[tuple[str, str]] = set()
        self._records: list[AuditRecord] = []
        self._pending: tuple[datetime, tuple[MarketInterval, ...], tuple[EntryApproval, ...]] | None = None
        self._next: datetime | None = None
        self._checkpoint = None
        self._replay_generation = 0
        self._pause_phase = 'OPEN'

    @property
    def audit(self) -> tuple[AuditRecord, ...]:
        return tuple(self._records)

    def _audit(self, event: str, at: datetime, *, approval: EntryApproval | None = None,
               reason: ReasonCode | None = None, **details: AuditValue) -> None:
        details['replay_generation'] = self._replay_generation
        self._records.append(AuditRecord(self.run_id, self.data_version, event, at,
            security_id=approval.security_id if approval else None,
            ticker=approval.ticker if approval else None, setup_id=approval.setup_id if approval else None,
            modeled_event_at=at, reason=reason, status=self.state.run_status if self.state else None,
            details=tuple(details.items())))

    def _result(self, start: int, decisions: tuple[CandidateDecision, ...] = ()) -> PortfolioStep:
        return PortfolioStep(self.state, decisions, tuple(self._records[start:]),
                             self.state.status == PortfolioStatus.PAUSED_DATA,
                             self.state.status == PortfolioStatus.INCOMPLETE)

    def start_session(self, day: date) -> PortfolioStep:
        if self.state is not None and self.state.status != PortfolioStatus.SESSION_CLOSED:
            raise ValueError('Previous session must be completely resolved before another session')
        session = self.calendar.session_for(day)
        if session is None:
            raise ValueError('Cannot initialize a verified non-session')
        if self.state is not None and day <= self.state.trading_date:
            raise ValueError('Sessions must advance chronologically')
        equity = self.config.starting_equity_usd if self.state is None else self.state.ending_equity
        if decimal(equity) <= 0:
            raise ValueError('No positive resolved equity available for another session')
        self.state = PortfolioState(session, equity, equity, equity, Decimal(0), equity, Decimal(0),
                                    last_event_at=session.open)
        self._next, self._pending, self._checkpoint = session.open, None, None
        start = len(self._records)
        self._audit('PORTFOLIO_SESSION_STARTED', session.open, trading_date=str(day),
                    bod_equity=equity, cash=equity, positions=0, daily_counters_reset=True)
        return self._result(start)

    def _active(self) -> None:
        if self.state is None or self.state.status != PortfolioStatus.ACTIVE:
            raise ValueError('Portfolio not active: paused, incomplete or session closed')

    def _sync(self, openings: dict[str, MarketInterval] | None = None) -> None:
        positions = tuple(manager.state for manager in sorted(self._managers.values(),
                          key=lambda m: m.state.entry.approval.ticker))
        report = Decimal(0)
        for position in positions:
            if position.last_trustworthy_price is None:
                report = None
                break
            report = add(report, multiply(position.last_trustworthy_price, Decimal(position.remaining_quantity)))
        exposure = Decimal(0)
        for position in positions:
            bar = openings.get(position.entry.approval.security_id) if openings is not None else None
            if bar is None or bar.classification != K.TRADED:
                exposure = None
                break
            exposure = add(exposure, multiply(bar.open, Decimal(position.remaining_quantity)))
        self.state = replace(self.state, positions=positions, exposure=exposure,
            current_equity=add(self.state.cash, exposure) if exposure is not None else None,
            reporting_exposure=report, reporting_equity=add(self.state.cash, report) if report is not None else None)

    def _locks(self, at: datetime) -> None:
        reasons = list(self.state.lockout_reasons)
        conditions = (
            (self.state.new_entries >= self.config.max_daily_entries, LockoutReason.DAILY_ENTRY_LIMIT),
            (self.state.realized_net <= multiply(self.state.bod_equity, self.config.daily_net_realized_loss_fraction_bod),
             LockoutReason.DAILY_NET_REALIZED_LOSS),
            (self.state.consecutive_losses >= self.config.max_consecutive_net_losing_trades,
             LockoutReason.CONSECUTIVE_NET_LOSSES))
        for failed, reason in conditions:
            if failed and reason not in reasons:
                reasons.append(reason)
                self._audit('PORTFOLIO_LOCKOUT_TRIGGERED', at, lockout_reason=reason.value,
                            entries=self.state.new_entries, realized_net=self.state.realized_net,
                            loss_threshold=multiply(self.state.bod_equity, self.config.daily_net_realized_loss_fraction_bod),
                            consecutive_losses=self.state.consecutive_losses, sticky=True)
        self.state = replace(self.state, lockout_reasons=tuple(reasons))

    def _apply_management(self, security: str, result: ManagementResult, at: datetime) -> None:
        self._records.extend(result.audit)
        for fill in result.exit_fills:
            costs = add(fill.commission, fill.fees)
            self.state = replace(self.state,
                cash=add(self.state.cash, subtract(fill.gross_proceeds, costs)),
                realized_gross=add(self.state.realized_gross, fill.gross_pnl),
                commissions=add(self.state.commissions, fill.commission), fees=add(self.state.fees, fill.fees),
                slippage=add(self.state.slippage, fill.slippage), realized_net=add(self.state.realized_net, fill.net_pnl))
            self._audit('PORTFOLIO_EXIT_ACCOUNTING', fill.available_at, security=security,
                        quantity=fill.quantity, cash=self.state.cash, gross_pnl=fill.gross_pnl,
                        net_pnl=fill.net_pnl, realized_daily_net=self.state.realized_net,
                        commission=fill.commission, fees=fill.fees, slippage=fill.slippage,
                        proceeds=fill.gross_proceeds, intrabar_time_unknown=fill.modeled_event_at is None)
            self._locks(fill.available_at)
        if result.state.completed is not None:
            completed = result.state.completed
            losses = self.state.consecutive_losses+1 if completed.net_pnl < 0 else 0
            self.state = replace(self.state, consecutive_losses=losses,
                                 completed_trades=self.state.completed_trades+1)
            self.completed += (completed,)
            self._quarantines[security] = completed
            del self._managers[security]
            self._audit('PORTFOLIO_TRADE_COMPLETED', at, security=security,
                        net_pnl=completed.net_pnl, outcome=completed.outcome.value,
                        completed_trades=self.state.completed_trades, consecutive_losses=losses,
                        quarantine_start=completed.exit_interval_start,
                        earliest_fresh_interval=completed.earliest_fresh_interval_start)
            self._locks(at)
        if result.requires_shared_stop:
            self.state = replace(self.state, status=PortfolioStatus.INCOMPLETE,
                                 run_status=result.state.run_status, ending_equity=None)
            self._audit('PORTFOLIO_INCOMPLETE', at, security=security,
                        underlying_cause=result.state.data_quality_reason,
                        unresolved_quantity=result.state.remaining_quantity, ending_equity=None)
        elif result.requires_shared_pause:
            self.state = replace(self.state, status=PortfolioStatus.PAUSED_DATA)

    def price_pattern_permitted(self, bar: MarketInterval) -> bool:
        """Occupancy/quarantine gate only, not detection or a universe filter."""
        if bar.security_id in self._managers or any(m.state.entry.approval.ticker == bar.ticker
                                                  for m in self._managers.values()):
            return False
        prior = self._quarantines.get(bar.security_id)
        return bar.classification == K.TRADED and (prior is None or prior.permits_fresh_price_candle(bar))

    def _fresh(self, approval: EntryApproval) -> bool:
        prior = self._quarantines.get(approval.security_id)
        if prior is None:
            return True
        prices = (approval.a, approval.b_origin, approval.d, approval.c_evidence.c_origin,
                  *approval.c_evidence.development_candles, *approval.c_evidence.higher_low_candles,
                  *approval.c_evidence.consolidation_candles, *approval.c_evidence.volume_candles)
        return all(bar.timestamp >= prior.earliest_fresh_interval_start for bar in prices)

    def _snapshot(self, at: datetime, bars: dict[str, MarketInterval]) -> PortfolioSnapshot:
        held = tuple(HeldPosition(position.entry.trade_id, position.entry.approval.security_id,
                   position.entry.approval.ticker, position.remaining_quantity,
                   bars[position.entry.approval.security_id].open, at, at,
                   bars[position.entry.approval.security_id].source_id,
                   position.entry.approval.eligibility.share_basis_id) for position in self.state.positions)
        return PortfolioSnapshot(at, at, self.state.bod_equity, self.state.current_equity,
            self.state.cash, self.state.exposure, held, not self.state.locked, 'phase8-derived-account',
            '|'.join(reason.value for reason in self.state.lockout_reasons) or None)

    def on_open(self, at: datetime, intervals: tuple[MarketInterval, ...],
                approvals: tuple[EntryApproval, ...] = ()) -> PortfolioStep:
        self._active()
        validate_timestamp(at)
        if self._pending is not None:
            raise ValueError('Complete the preceding interval before another OPEN')
        if (at < self._next or (self._managers and at != self._next)
                or not self.state.session.open <= at < self.state.session.close
                or at.second or at.microsecond):
            raise ValueError('Supply chronological RTH minute OPENs; do not bridge held intervals')
        bars = {bar.security_id: bar for bar in intervals}
        if len(bars) != len(intervals) or any(bar.timestamp != at for bar in intervals):
            raise ValueError('Duplicate/conflicting or wrong-timestamp intervals')
        ranked = rank_candidates(approvals)
        for approval in ranked:
            if (approval.scheduled_timestamp != at or approval.session != self.state.session
                    or (approval.security_id, approval.setup_id) in self._consumed):
                raise ValueError('Candidate must belong to this unique scheduled opportunity/session')
            bar = bars.get(approval.security_id)
            if bar is None or bar.classification != K.TRADED or bar.open != approval.reference_open or bar.ticker != approval.ticker:
                raise ValueError('Approved candidate requires its matching trustworthy scheduled OPEN')
        for security, manager in self._managers.items():
            if security not in bars or bars[security].ticker != manager.state.entry.approval.ticker:
                raise ValueError('Represent every held interval explicitly, including missing/invalid data')
        start = len(self._records)
        bad = [security for security in self._managers if bars[security].classification in (K.MISSING, K.INVALID)]
        if bad:
            self._pause_phase = 'OPEN'
            memo = {id(self.calendar): self.calendar, id(self.ticks): self.ticks}
            self._checkpoint = (self.state, deepcopy(self._managers, memo), deepcopy(self.constructor),
                                self.completed, self._quarantines.copy(), self._consumed.copy(), self._next)
        else:
            self._checkpoint = None
        self._pending = at, intervals, approvals
        self.state = replace(self.state, last_event_at=at)
        # Unknown held activity pauses the whole timestamp before any account effects.
        if bad:
            for security in sorted(bad, key=lambda s: bars[s].ticker):
                result = self._managers[security].on_open(bars[security])
                self._apply_management(security, result, at)
                self._audit('PORTFOLIO_DATA_PAUSE', at, security=security,
                            data_quality_reason=bars[security].data_quality_reason, shared_timestamp_not_processed=True)
            self._sync()
            return self._result(start)
        self._audit('PORTFOLIO_OPEN_PHASE', at, phase='REQUIRED_EXITS_BEFORE_ENTRIES')
        for security in sorted(tuple(self._managers), key=lambda s: bars[s].ticker):
            self._apply_management(security, self._managers[security].on_open(bars[security]), at)
            if self.state.status != PortfolioStatus.ACTIVE:
                self._sync()
                return self._result(start)
        self._sync(bars)
        self._audit('PORTFOLIO_OPEN_VALUATION', at, cash=self.state.cash, equity=self.state.current_equity,
                    exposure=self.state.exposure, positions=self.state.open_position_count,
                    authoritative_open_available=self.state.current_equity is not None)
        self._audit('PORTFOLIO_CANDIDATE_ORDER', at, order='|'.join(a.ticker for a in ranked),
                    rule='D_PREVIOUS20_RVOL_DESC_THEN_ALPHABETICAL_TICKER')
        decisions = []
        for rank, approval in enumerate(ranked, 1):
            self._consumed.add((approval.security_id, approval.setup_id))
            before_cash, before_exposure = self.state.cash, self.state.exposure
            self._audit('PORTFOLIO_CANDIDATE', at, approval=approval, rank=rank,
                        d_rvol=str(approval.d_attempt.previous_20.ratio), cash=before_cash,
                        equity=self.state.current_equity, exposure=before_exposure,
                        lockout_reasons='|'.join(reason.value for reason in self.state.lockout_reasons))
            construction, reason = None, None
            if not self._fresh(approval):
                description = 'FINAL_EXIT_QUARANTINE_PRICE_HISTORY'
            elif self.state.current_equity is None:
                description = 'PORTFOLIO_VALUATION_UNAVAILABLE'
            else:
                construction = self.constructor.construct(approval, self._snapshot(at, bars), self.ticks,
                    trade_id=f'{self.run_id}:{approval.security_id}:{approval.setup_id}')
                self._records.extend(construction.audit)
                description, reason = construction.description, construction.reason
            accepted = construction is not None and construction.position is not None
            if accepted:
                position = construction.position
                manager = PositionManager(position, self.calendar, self.ticks)
                self._managers[approval.security_id] = manager
                self.state = replace(self.state, cash=position.account_after.available_cash,
                                     new_entries=self.state.new_entries+1)
                self._sync(bars)
                self._locks(at)
                # The new position's entry-minute later extrema wait for on_close().
                self._apply_management(approval.security_id, manager.on_open(bars[approval.security_id]), at)
            self._audit('PORTFOLIO_ALLOCATION', at, approval=approval, rank=rank,
                        accepted=accepted, description=description, consumed=not accepted,
                        cash_before=before_cash, cash_after=self.state.cash,
                        exposure_before=before_exposure, exposure_after=self.state.exposure,
                        entries=self.state.new_entries, positions=self.state.open_position_count)
            decisions.append(CandidateDecision(approval, rank, accepted, description, reason, not accepted,
                             construction, before_cash, self.state.cash, before_exposure, self.state.exposure))
        return self._result(start, tuple(decisions))

    def on_close(self, at: datetime, intervals: tuple[MarketInterval, ...] | None = None) -> PortfolioStep:
        self._active()
        if self._pending is None or at != self._pending[0]+timedelta(minutes=1):
            raise ValueError('Completion must follow the pending minute OPEN')
        start = len(self._records)
        bars = {bar.security_id: bar for bar in self._pending[1]}
        if intervals is not None:
            replacement={bar.security_id:bar for bar in intervals}
            if (len(replacement)!=len(intervals) or any(bar.timestamp!=self._pending[0] for bar in intervals)
                    or any(security not in replacement for security in self._managers)):
                raise ValueError('Supply unique completed records for every held pending interval')
            for security in self._managers:
                original, completed = bars[security], replacement[security]
                if (completed.ticker != original.ticker or
                        (completed.classification not in (K.MISSING, K.INVALID) and
                         (completed.classification != original.classification or completed.open != original.open))):
                    raise ValueError('Completion cannot revise an already-known OPEN; rerun corrected inputs')
            bars.update(replacement)
            bad=[security for security in self._managers if bars[security].classification in (K.MISSING,K.INVALID)]
            if bad:
                memo={id(self.calendar):self.calendar,id(self.ticks):self.ticks}
                self._checkpoint=(self.state,deepcopy(self._managers,memo),deepcopy(self.constructor),
                                  self.completed,self._quarantines.copy(),self._consumed.copy(),self._next)
                self._pause_phase='COMPLETION'
                self._pending=self._pending[0],tuple(bars.values()),self._pending[2]
                self.state=replace(self.state,last_event_at=at)
                for security in sorted(bad,key=lambda s:bars[s].ticker):
                    self._apply_management(security,self._managers[security].complete_interval(bars[security]),at)
                self._sync()
                return self._result(start)
        self.state = replace(self.state, last_event_at=at)
        self._audit('PORTFOLIO_COMPLETION_PHASE', at, phase='INTRABAR_EXITS_THEN_PROSPECTIVE_UPDATES')
        for security in sorted(tuple(self._managers), key=lambda s: bars[s].ticker):
            manager=self._managers[security]
            result=manager.complete_interval(bars[security]) if intervals is not None else manager.on_close(bars[security])
            self._apply_management(security,result,at)
            if self.state.status != PortfolioStatus.ACTIVE:
                self._sync()
                return self._result(start)
        self._sync()
        self._pending, self._next = None, at
        self._audit('PORTFOLIO_COMPLETION_ACCOUNT', at, cash=self.state.cash,
                    reporting_equity=self.state.reporting_equity, reporting_exposure=self.state.reporting_exposure,
                    positions=self.state.open_position_count, realized_net=self.state.realized_net)
        if at == self.state.session.close:
            self.close_session(at)
        return self._result(start)

    def close_session(self, at: datetime) -> PortfolioStep:
        self._active()
        if at != self.state.session.close or self._pending is not None or self._managers:
            raise ValueError('Only an official-close, fully resolved portfolio can finalize equity')
        start = len(self._records)
        self.state = replace(self.state, status=PortfolioStatus.SESSION_CLOSED, ending_equity=self.state.cash,
                             current_equity=self.state.cash, exposure=Decimal(0), last_event_at=at)
        self._audit('PORTFOLIO_SESSION_CLOSED', at, ending_equity=self.state.cash,
                    entries=self.state.new_entries, realized_net=self.state.realized_net,
                    lockout_reasons='|'.join(reason.value for reason in self.state.lockout_reasons),
                    no_overnight_positions=True, signal_session_reset_required=True)
        return self._result(start)

    def resume_with_replacements(self, replacements: tuple[MarketInterval, ...]) -> PortfolioStep:
        if self.state is None or self.state.status != PortfolioStatus.PAUSED_DATA or self._pending is None:
            raise ValueError('Only a paused shared timestamp can be replayed')
        at, bars, approvals = self._pending
        changed = {bar.security_id: bar for bar in replacements}
        bad = {bar.security_id for bar in bars if bar.security_id in self._managers
               and bar.classification in (K.MISSING, K.INVALID)}
        if (len(changed) != len(replacements) or set(changed) != bad
                or any(bar.timestamp != at or bar.classification not in (K.TRADED, K.NO_TRADE)
                       for bar in replacements)):
            raise ValueError('Supply trustworthy exact-interval replacements for every paused held security')
        if self._pause_phase == 'COMPLETION':
            checkpoint_managers = self._checkpoint[1]
            for security, bar in changed.items():
                original = checkpoint_managers[security]._pending
                if bar.classification != original.classification or bar.open != original.open or bar.ticker != original.ticker:
                    raise ValueError('Completion replay cannot revise the already-known OPEN')
        start = len(self._records)
        (self.state, self._managers, self.constructor, self.completed,
         self._quarantines, self._consumed, self._next) = self._checkpoint
        self._pending = None
        self._replay_generation += 1
        replay_at = at+timedelta(minutes=1) if self._pause_phase == 'COMPLETION' else at
        self._audit('PORTFOLIO_SHARED_CHECKPOINT_REPLAY', replay_at,
                    replaced='|'.join(sorted(changed)), historical_replacement=True)
        if self._pause_phase=='COMPLETION':
            self._pending=at,tuple(changed.get(bar.security_id,bar) for bar in bars),approvals
            result=self.on_close(at+timedelta(minutes=1),self._pending[1])
        else:
            result = self.on_open(at, tuple(changed.get(bar.security_id, bar) for bar in bars), approvals)
        return self._result(start, result.candidates)

    def mark_data_incomplete(self) -> PortfolioStep:
        if self.state is None or self.state.status != PortfolioStatus.PAUSED_DATA:
            raise ValueError('Only unresolved paused data can be marked incomplete')
        start = len(self._records)
        for security, manager in self._managers.items():
            if manager.state.status.value == 'PAUSED_DATA':
                self._apply_management(security, manager.mark_data_incomplete(), self.state.last_event_at)
        self.state = replace(self.state, status=PortfolioStatus.INCOMPLETE, ending_equity=None)
        self._sync()
        return self._result(start)
