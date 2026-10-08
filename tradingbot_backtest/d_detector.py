"""Completed-minute breakout qualification and signal handoff; never fills."""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from fractions import Fraction

from .ab_detector import EligibilityInputs
from .audit import AuditRecord
from .c_detector import CDetector, CLockEvidence
from .codes import ReasonCode, SetupTermination
from .config import FROZEN_V1, StrategyConfig
from .market import IntervalClassification as Kind, MarketInterval
from .sessions import NEW_YORK, SessionCalendar, SessionPeriod, TradingSession
from .states import StrategyState as State
from .volume import VolumeEvidence


@dataclass(frozen=True, slots=True)
class BreakoutAttempt:
    number: int
    candle: MarketInterval
    close_extension: Fraction
    previous_20: VolumeEvidence
    local_intervals: tuple[MarketInterval, ...]
    local_average: Fraction | None
    price_passed: bool
    rvol_passed: bool
    local_volume_passed: bool
    failures: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PendingEntrySignal:
    setup_id: str
    security_id: str
    ticker: str
    a: MarketInterval
    b_price: Decimal
    b_origin: MarketInterval
    c_price: Decimal
    c_evidence: CLockEvidence
    d: MarketInterval
    d_confirmation_timestamp: datetime
    scheduled_interval: datetime
    attempt: BreakoutAttempt


@dataclass(frozen=True, slots=True)
class EntryHandoff:
    """Unevaluated OPEN inputs for the future execution phase, not a fill.

    The interval is supplied at completion. Future opening decisions must use
    only its open and inputs knowable at the modeled OPEN, never later OHLC.
    """
    signal: PendingEntrySignal
    interval: MarketInterval
    eligibility_inputs: EligibilityInputs
    modeled_open_timestamp: datetime


@dataclass(frozen=True, slots=True)
class DState:
    setup_id: str | None = None
    wait_elapsed_minutes: int = 0
    resolution_elapsed_minutes: int = 0
    first_attempt_timestamp: datetime | None = None
    attempts: tuple[BreakoutAttempt, ...] = ()
    confirmed_d: MarketInterval | None = None
    signal: PendingEntrySignal | None = None
    handoff: EntryHandoff | None = None
    cancellation_reason: ReasonCode | None = None


class DDetector(CDetector):
    """A/B/C extension with elapsed timers and exactly one scheduled signal.

    After handoff, the caller must transfer ownership to a future execution
    component before feeding more intervals. There is no execution component
    or assumed trade acceptance in Phase 4.
    """

    def __init__(self, security_id: str, ticker: str, calendar: SessionCalendar,
                 *, run_id: str, data_version: str, config: StrategyConfig = FROZEN_V1):
        super().__init__(security_id, ticker, calendar, run_id=run_id,
                         data_version=data_version, config=config)
        self.d_state = DState()

    def feed(self, bar: MarketInterval, inputs: EligibilityInputs) -> tuple[AuditRecord, ...]:
        if self.d_state.handoff is not None:
            raise RuntimeError("Entry handoff requires future execution ownership before further feeds")
        previous_lock = self.c_state.lock_timestamp
        super().feed(bar, inputs)
        if self.state.setup_id != self.d_state.setup_id:
            self.d_state = DState(setup_id=self.state.setup_id)
        if self.state.lifecycle == State.C_LOCKED and self.c_state.lock_timestamp != previous_lock:
            self._audit("LOCKED_C_WAIT_STARTED", bar, first_interval=self.c_state.lock_timestamp,
                        expiration=self.c_state.lock_timestamp + timedelta(
                            minutes=self.config.locked_c_max_first_attempt_elapsed_minutes))
        return tuple(self._events)

    def _process_active(self, evidence: VolumeEvidence) -> None:
        if self.state.lifecycle in (State.C_LOCKED, State.BREAKOUT_ATTEMPT):
            self._process_breakout_interval(evidence.candle, evidence)
        else:
            super()._process_active(evidence)

    def _process_no_trade(self, bar: MarketInterval) -> None:
        super()._process_no_trade(bar)
        if self.active and self.state.lifecycle in (State.C_LOCKED, State.BREAKOUT_ATTEMPT):
            self._process_breakout_interval(bar, None)

    def _process_breakout_interval(self, bar: MarketInterval,
                                   evidence: VolumeEvidence | None) -> None:
        d = self.d_state
        if evidence is not None:
            self._check_locked(bar)
            if not self.active:
                return
        if d.first_attempt_timestamp is None:
            self.d_state = replace(d, wait_elapsed_minutes=d.wait_elapsed_minutes + 1)
            self._audit("LOCKED_C_WAIT", bar, lock_timestamp=self.c_state.lock_timestamp,
                        elapsed_minutes=self.d_state.wait_elapsed_minutes,
                        expiration=self.c_state.lock_timestamp + timedelta(
                            minutes=self.config.locked_c_max_first_attempt_elapsed_minutes),
                        maximum=self.config.locked_c_max_first_attempt_elapsed_minutes)
        else:
            self.d_state = replace(d, resolution_elapsed_minutes=d.resolution_elapsed_minutes + 1)
        if evidence is not None and bar.high > self.state.provisional_b_price:
            self._attempt(evidence)
            if self.state.lifecycle in (State.PENDING_ENTRY, State.TERMINATED):
                return
        d = self.d_state
        if d.first_attempt_timestamp is not None:
            self._audit("BREAKOUT_RESOLUTION_PROGRESS", bar,
                        first_attempt=d.first_attempt_timestamp,
                        expiration=d.first_attempt_timestamp + timedelta(
                            minutes=self.config.breakout_resolution_elapsed_minutes),
                        elapsed_minutes=d.resolution_elapsed_minutes,
                        maximum=self.config.breakout_resolution_elapsed_minutes)
            if d.resolution_elapsed_minutes >= self.config.breakout_resolution_elapsed_minutes:
                self._terminate(bar, "BREAKOUT_RESOLUTION_WINDOW_EXHAUSTED", SetupTermination.EXPIRED)
        elif d.wait_elapsed_minutes >= self.config.locked_c_max_first_attempt_elapsed_minutes:
            self._terminate(bar, "LOCKED_C_WAIT_EXHAUSTED", SetupTermination.EXPIRED)

    def _attempt(self, evidence: VolumeEvidence) -> None:
        bar, d = evidence.candle, self.d_state
        if d.first_attempt_timestamp is None:
            d = replace(d, first_attempt_timestamp=bar.timestamp, resolution_elapsed_minutes=1)
        extension = (Fraction(bar.close) - Fraction(self.state.provisional_b_price)) / Fraction(
            self.state.provisional_b_price)
        local = evidence.preceding[-self.config.d_local_volume_previous_intervals:]
        session = self.calendar.session_for(bar.timestamp.astimezone(NEW_YORK).date())
        local_valid = (len(local) == self.config.d_local_volume_previous_intervals
                       and all(session.period_at(item.timestamp,
                                                 premarket_start=self.config.premarket_volume_history_start_et)
                               == SessionPeriod.REGULAR for item in local))
        average = (sum((Fraction(item.volume) for item in local), Fraction()) / len(local)
                   if local_valid else None)
        price_passed = self.config.d_min_close_buffer_above_b <= extension <= self.config.d_max_close_extension_above_b
        rvol_passed = evidence.qualifies(Fraction(self.config.d_min_rvol))
        local_passed = average is not None and Fraction(bar.volume) > average
        failures = []
        if extension > self.config.d_max_close_extension_above_b:
            failures.append("D_CLOSE_OVEREXTENDED")
        elif not price_passed:
            failures.append("D_CLOSE_BUFFER_FAILED")
        if not rvol_passed:
            failures.append("RVOL_BASELINE_ZERO" if evidence.baseline == 0 else "D_RVOL_FAILED")
        if not local_passed:
            failures.append("D_LOCAL_VOLUME_FAILED")
        attempt = BreakoutAttempt(len(d.attempts) + 1, bar, extension, evidence, local, average,
                                 price_passed, rvol_passed, local_passed, tuple(failures))
        self.d_state = replace(d, attempts=d.attempts + (attempt,))
        self.state = replace(self.state, lifecycle=State.BREAKOUT_ATTEMPT)
        self._audit("BREAKOUT_ATTEMPT", bar, attempt_number=attempt.number,
                    failures="|".join(failures), close_extension=str(extension),
                    price_passed=price_passed, rvol_passed=rvol_passed, local_passed=local_passed,
                    previous_20_baseline=str(evidence.baseline) if evidence.baseline is not None else None,
                    rvol_ratio=str(evidence.ratio) if evidence.ratio is not None else None,
                    local_average=str(average) if average is not None else None,
                    local_intervals="|".join(
                        f"{item.timestamp.isoformat()},{item.classification},{item.volume}" for item in local),
                    resolution_elapsed_minutes=self.d_state.resolution_elapsed_minutes,
                    resolution_expiration=self.d_state.first_attempt_timestamp + timedelta(
                        minutes=self.config.breakout_resolution_elapsed_minutes),
                    remaining_attempts=self.config.max_total_breakout_attempts - attempt.number)
        if extension > self.config.d_max_close_extension_above_b:
            self._terminate(bar, "D_CLOSE_OVEREXTENDED", SetupTermination.EXPIRED)
        elif not failures:
            self._confirm(attempt)
        elif attempt.number >= self.config.max_total_breakout_attempts:
            self._terminate(bar, "BREAKOUT_ATTEMPT_LIMIT_EXHAUSTED", SetupTermination.EXPIRED)
        else:
            self._audit("BREAKOUT_ATTEMPT_FAILED", bar, attempt_number=attempt.number,
                        failures="|".join(failures))

    def _confirm(self, attempt: BreakoutAttempt) -> None:
        bar = attempt.candle
        self.state = replace(self.state, lifecycle=State.D_CONFIRMED)
        self._audit("D_CONFIRMED", bar, d_close=bar.close, confirmed_at=bar.end,
                    b_price=self.state.provisional_b_price, c_price=self.c_state.locked_price)
        signal = PendingEntrySignal(self.state.setup_id, self.security_id, self.ticker,
                                    self.state.a, self.state.provisional_b_price, self.state.b_origin,
                                    self.c_state.locked_price, self.c_state.lock_evidence,
                                    bar, bar.end, bar.end, attempt)
        self.d_state = replace(self.d_state, confirmed_d=bar, signal=signal)
        self.state = replace(self.state, lifecycle=State.PENDING_ENTRY)
        self._audit("PENDING_ENTRY_SCHEDULED", bar, scheduled_interval=signal.scheduled_interval,
                    d_confirmation=signal.d_confirmation_timestamp)

    def _process_pending_interval(self, bar: MarketInterval, inputs: EligibilityInputs,
                                  session: TradingSession) -> bool:
        if self.state.lifecycle != State.PENDING_ENTRY:
            return False
        signal = self.d_state.signal
        if bar.timestamp != signal.scheduled_interval:
            raise ValueError("Pending signal requires its one scheduled next interval")
        reason = None
        if (session.period_at(bar.timestamp, premarket_start=self.config.premarket_volume_history_start_et)
                != SessionPeriod.REGULAR or bar.timestamp >= session.close - self.config.entry_cutoff_before_session_close):
            reason = ReasonCode.ENTRY_TIME_RESTRICTION
        elif bar.classification == Kind.MISSING:
            reason = ReasonCode.ENTRY_DATA_MISSING
        elif bar.classification == Kind.INVALID:
            reason = ReasonCode.ENTRY_DATA_INVALID
        elif bar.classification == Kind.NO_TRADE:
            reason = ReasonCode.ENTRY_NO_TRADE
        if reason is not None:
            self.d_state = replace(self.d_state, cancellation_reason=reason)
            self._terminate(bar, "PENDING_ENTRY_CANCELED", SetupTermination.CONSUMED, reason)
            if bar.classification in (Kind.MISSING, Kind.INVALID):
                self._volume.clear()
            self._audit("PENDING_ENTRY_CANCELED", bar, reason=reason, scheduled_interval=bar.timestamp,
                        data_quality_reason=bar.data_quality_reason)
        else:
            self.d_state = replace(self.d_state, handoff=EntryHandoff(signal, bar, inputs, bar.timestamp))
            self._audit("PENDING_ENTRY_HANDOFF", bar, scheduled_open=bar.open,
                        modeled_open_timestamp=bar.timestamp, executed=False,
                        entry_checks_deferred=True)
            self._events[-1] = replace(self._events[-1], modeled_event_at=bar.timestamp)
        if bar.classification in (Kind.TRADED, Kind.NO_TRADE):
            self._volume.append(bar)
            self._volume = self._volume[-self.config.rvol_previous_intervals:]
        return True
