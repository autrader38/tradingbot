"""Completed-minute, per-security A/B detector. Stops price progression at B.

No provider, C/D logic, orders, fills or account state lives in this module.
Inputs must already use a verified compatible point-in-time share basis.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from fractions import Fraction

from .audit import AuditRecord
from .codes import ReasonCode, SetupTermination
from .config import FROZEN_V1, StrategyConfig
from .market import IntervalClassification as Kind, MarketInterval
from .numerics import decimal
from .sessions import NEW_YORK, SessionCalendar, SessionPeriod
from .states import StrategyState as State
from .volume import VolumeEvidence, volume_evidence


@dataclass(frozen=True, slots=True)
class EligibilityInputs:
    """Already verified static gates and compatible official prior close.

    No market-cap, ADV, ADR or corporate-action derivation is performed here.
    """
    prior_regular_close: Decimal
    static_eligible: bool
    share_basis_id: str

    def __post_init__(self) -> None:
        if type(self.prior_regular_close) is not Decimal:
            raise TypeError("Prior close requires an exact Decimal")
        if decimal(self.prior_regular_close) <= 0:
            raise ValueError("Prior close must be trustworthy and positive")
        if (type(self.static_eligible) is not bool or not isinstance(self.share_basis_id, str)
                or not self.share_basis_id.strip()):
            raise ValueError("Supply verified eligibility and share-basis provenance")


@dataclass(frozen=True, slots=True)
class ABState:
    lifecycle: State = State.INELIGIBLE
    setup_id: str | None = None
    a: MarketInterval | None = None
    activation_timestamp: datetime | None = None
    activation_trigger_high: Decimal | None = None
    activation_rise: Fraction | None = None
    activation_volume_participants: tuple[VolumeEvidence, ...] = ()
    provisional_b_price: Decimal | None = None
    b_origin: MarketInterval | None = None
    origin_retests: tuple[datetime, ...] = ()
    impulse_candles: tuple[MarketInterval, ...] = ()
    impulse_qualified: bool = False
    confirmation_candles: tuple[MarketInterval, ...] = ()
    logical_b_confirmation_timestamp: datetime | None = None
    impulse_volumes: tuple[Decimal, ...] = ()
    impulse_volume_average: Fraction | None = None
    frozen_impulse_volume_average: Fraction | None = None
    termination: SetupTermination | None = None
    termination_reason: ReasonCode | None = None
    termination_description: str | None = None
    termination_timestamp: datetime | None = None

    @property
    def traded_candle_count(self) -> int:
        return len(self.impulse_candles)


class ABDetector:
    """Feed every elapsed eligible interval explicitly, in strict time order.

    Unrepresented eligible minutes are rejected as an ingestion contract error;
    callers must submit MISSING, not silently omit them. Audit records returned
    by feed contain all events for that completed interval, including replay.
    """

    def __init__(self, security_id: str, ticker: str, calendar: SessionCalendar,
                 *, run_id: str, data_version: str, config: StrategyConfig = FROZEN_V1):
        self.security_id, self.ticker = security_id, ticker
        self.calendar, self.config = calendar, config
        self.run_id, self.data_version = run_id, data_version
        self.state = ABState()
        self._price: list[VolumeEvidence] = []
        self._volume: list[MarketInterval] = []
        self._participants: list[VolumeEvidence] = []
        self._previous: MarketInterval | None = None
        self._date = None
        self._basis: str | None = None
        self._eligibility_inputs: EligibilityInputs | None = None
        self._serial = 0
        self._needs_restoration = False
        self._events: list[AuditRecord] = []

    def _audit(self, event: str, bar: MarketInterval, *, reason: ReasonCode | None = None,
               **details: str | int | bool | Decimal | datetime | None) -> None:
        self._events.append(AuditRecord(
            run_id=self.run_id, data_version=self.data_version, event_type=event,
            recorded_at=self._current.end, security_id=self.security_id, ticker=self.ticker,
            setup_id=self.state.setup_id, interval_start=bar.timestamp, interval_end=bar.end,
            modeled_event_at=bar.end, state=self.state.lifecycle, reason=reason,
            details=tuple(details.items())))

    def _terminate(self, bar: MarketInterval, description: str,
                   termination: SetupTermination = SetupTermination.INVALIDATED,
                   reason: ReasonCode | None = None) -> None:
        self.state = replace(self.state, lifecycle=State.TERMINATED, termination=termination,
                             termination_reason=reason, termination_description=description,
                             termination_timestamp=bar.end)
        self._price.clear()
        self._audit("SETUP_TERMINATED", bar, reason=reason, description=description,
                    termination=termination.value)

    @property
    def active(self) -> bool:
        return self.state.setup_id is not None and self.state.lifecycle != State.TERMINATED

    def _dynamic_reason(self, price: Decimal, inputs: EligibilityInputs) -> ReasonCode | None:
        price_failed = price > self.config.max_price_usd
        change_failed = ((Fraction(price) - Fraction(inputs.prior_regular_close))
                         / Fraction(inputs.prior_regular_close) < self.config.min_current_day_change)
        if price_failed and change_failed:
            return ReasonCode.DYNAMIC_MULTIPLE_ELIGIBILITY_FAILURES
        if price_failed:
            return ReasonCode.DYNAMIC_PRICE_ABOVE_MAX
        if change_failed:
            return ReasonCode.DYNAMIC_DAILY_CHANGE_BELOW_MIN
        return None

    def feed(self, bar: MarketInterval, inputs: EligibilityInputs) -> tuple[AuditRecord, ...]:
        """Process a completed interval, never its forming OHLC or future inputs."""
        if bar.security_id != self.security_id or bar.ticker != self.ticker:
            raise ValueError("Detector identity must match the interval")
        date = bar.timestamp.astimezone(NEW_YORK).date()
        session = self.calendar.session_for(date)
        if session is None:
            raise ValueError("No eligible session exists for this interval")
        period = session.period_at(bar.timestamp,
                                   premarket_start=self.config.premarket_volume_history_start_et)
        if period == SessionPeriod.OUTSIDE:
            raise ValueError("Supply only eligible regular/premarket volume intervals")
        if self._previous is not None:
            if bar.timestamp.astimezone(timezone.utc) <= self._previous.timestamp.astimezone(timezone.utc):
                raise ValueError("Intervals must be strictly increasing")
            if date == self._date and bar.timestamp != self._previous.end:
                raise ValueError("Represent every eligible missing minute explicitly")
            if date != self._date and self.active:
                previous_session = self.calendar.session_for(self._date)
                if previous_session is None or self._previous.end < previous_session.close:
                    raise ValueError("Represent remaining active previous-session minutes explicitly")
        if date == self._date and self._basis != inputs.share_basis_id:
            raise ValueError("Changed share basis requires verified input treatment; cannot mix units")
        if date == self._date and self._eligibility_inputs != inputs:
            raise ValueError("Verified day-level inputs changed; supply corrected history for replay")
        self._events = []
        self._current = bar
        if date != self._date:
            if self.active:
                self._terminate(self._previous, "SESSION_BOUNDARY", SetupTermination.EXPIRED)
            self._price.clear()
            self._volume.clear()
            self._participants.clear()
            self.state = ABState()
            self._date, self._basis = date, inputs.share_basis_id
            self._eligibility_inputs = inputs
            self._needs_restoration = False
        self._previous = bar
        self._audit("DATA_INTERVAL", bar, classification=bar.classification.value,
                    source_id=bar.source_id, open=bar.open, high=bar.high,
                    low=bar.low, close=bar.close, volume=bar.volume)
        if bar.classification in (Kind.MISSING, Kind.INVALID):
            reason = ReasonCode.DATA_GAP if bar.classification == Kind.MISSING else ReasonCode.INVALID_DATA
            if self.active:
                self._terminate(bar, bar.data_quality_reason, reason=reason)
            else:
                self.state = ABState()
                self._audit("HISTORY_RESET", bar, reason=reason, description=bar.data_quality_reason)
            self._price.clear()
            self._volume.clear()
            return tuple(self._events)
        evidence = None
        if bar.classification == Kind.TRADED:
            evidence = volume_evidence(bar, tuple(self._volume), self.config.rvol_previous_intervals)
            self._audit("RVOL_EVALUATED", bar,
                        reason=ReasonCode.RVOL_BASELINE_ZERO if evidence.baseline == 0 else None,
                        baseline_slots=len(evidence.preceding),
                        evaluated_volume=bar.volume,
                        baseline=str(evidence.baseline) if evidence.baseline is not None else None,
                        ratio=str(evidence.ratio) if evidence.ratio is not None else None,
                        baseline_intervals="|".join(
                            f"{item.timestamp.isoformat()},{item.classification},{item.volume}"
                            for item in evidence.preceding))
        self._volume.append(bar)
        self._volume = self._volume[-self.config.rvol_previous_intervals:]
        if period != SessionPeriod.REGULAR:
            return tuple(self._events)
        if bar.end >= session.close - self.config.entry_cutoff_before_session_close:
            if self.active:
                self._terminate(bar, "NO_PERMITTED_ENTRY_REMAINS", SetupTermination.EXPIRED)
            self._price.clear()
            return tuple(self._events)
        if evidence is None:
            self._audit("NO_TRADE_PRESERVED", bar)
            return tuple(self._events)
        reason = self._dynamic_reason(bar.close, inputs)
        self._audit("DYNAMIC_ELIGIBILITY", bar, reason=reason, close=bar.close,
                    prior_close=inputs.prior_regular_close,
                    static_eligible=inputs.static_eligible, share_basis_id=inputs.share_basis_id)
        if reason is not None:
            if self.active:
                self._terminate(bar, "Dynamic eligibility failed", reason=reason)
                self._needs_restoration = True
            else:
                self.state = ABState(lifecycle=State.INELIGIBLE)
                # Before activation, the dynamic gates prohibit activation,
                # not the existence of trustworthy RTH price history. After a
                # dynamic termination, restoration's fresh boundary controls.
                if not self._needs_restoration:
                    self._remember_price(evidence)
            return tuple(self._events)
        if self._needs_restoration:
            self._needs_restoration = False
            self._price.clear()
            self.state = ABState(lifecycle=State.ELIGIBLE)
            self._audit("ELIGIBILITY_RESTORED", bar)
            return tuple(self._events)
        if self.active:
            self._process_active(evidence)
            return tuple(self._events)
        if not inputs.static_eligible:
            self.state = ABState(lifecycle=State.INELIGIBLE)
            self._remember_price(evidence)
            return tuple(self._events)
        self.state = ABState(lifecycle=State.ELIGIBLE)
        if self._price:
            a_evidence = min(self._price, key=lambda item: item.candle.low)
            start = self._price.index(a_evidence)
            participants = self._price[start:] + [evidence]
            rise = (Fraction(bar.high) - Fraction(a_evidence.candle.low)) / Fraction(a_evidence.candle.low)
            qualifying = tuple(item for item in participants
                               if item.qualifies(Fraction(self.config.impulse_participant_min_rvol)))
            self._audit("A_SELECTED", bar, a_timestamp=a_evidence.candle.timestamp,
                        a_price=a_evidence.candle.low, trigger_high=bar.high, rise=str(rise))
            if rise >= self.config.a_activation_min_rise and qualifying:
                self._activate(participants, qualifying, rise)
                return tuple(self._events)
        self._remember_price(evidence)
        return tuple(self._events)

    def _process_active(self, evidence: VolumeEvidence) -> None:
        """Extension point; A/B-only callers retain the approved Phase 2 path."""
        if self.state.lifecycle != State.B_CONFIRMED:
            self._participants.append(evidence)
            self._advance(evidence)

    def _remember_price(self, evidence: VolumeEvidence) -> None:
        self._price.append(evidence)
        self._price = self._price[-self.config.a_selection_max_traded_lookback:]

    def _activate(self, participants: list[VolumeEvidence], qualifying: tuple[VolumeEvidence, ...],
                  rise: Fraction) -> None:
        self._serial += 1
        self._participants = participants.copy()
        candles = tuple(item.candle for item in participants)
        a, trigger = candles[0], candles[-1]
        window = candles[:self.config.impulse_max_traded_candles]
        origin = max(range(len(window)), key=lambda i: (window[i].high, i))
        self.state = ABState(
            lifecycle=State.A_CANDIDATE,
            setup_id=f"{self.run_id}:{self.security_id}:{self._date.isoformat()}:{self._serial}",
            a=a, activation_timestamp=trigger.end, activation_trigger_high=trigger.high,
            activation_rise=rise, activation_volume_participants=qualifying,
            provisional_b_price=window[origin].high, b_origin=window[origin],
            impulse_candles=window[:origin + 1],
            origin_retests=tuple(item.timestamp for item in window
                                if item.high == window[origin].high)[1:])
        self._audit("A_ACTIVATED", trigger, a_timestamp=a.timestamp, a_price=a.low,
                    rise=str(rise), qualifying_participants="|".join(
                        item.candle.timestamp.isoformat() for item in qualifying))
        self.state = replace(self.state, lifecycle=State.AB_IMPULSE)
        self._audit("AB_IMPULSE_STARTED", trigger)
        self._recalculate()
        self._audit("INITIAL_B_SELECTED", self.state.b_origin, b_price=self.state.provisional_b_price)
        # Apply the same confirmation/deadline rules to completed post-origin data.
        self._deadline(window[origin])
        for item in participants[origin + 1:]:
            if self.state.lifecycle in (State.TERMINATED, State.B_CONFIRMED):
                break
            self._advance(item)
        self._price.clear()

    def _recalculate(self) -> None:
        state = self.state
        origin_index = next(i for i, item in enumerate(self._participants)
                            if item.candle.timestamp == state.b_origin.timestamp)
        values = tuple(item.candle.volume for item in self._participants[:origin_index + 1])
        volume_ok = any(item.qualifies(Fraction(self.config.impulse_participant_min_rvol))
                        for item in self._participants[:origin_index + 1])
        # The A-origin high remains the recorded highest high, but cannot itself
        # prove sequential upward movement from its own low (Decision 45).
        price_ok = (origin_index > 0 and origin_index + 1 >= self.config.impulse_min_traded_candles and
                    (Fraction(state.provisional_b_price) - Fraction(state.a.low))
                    / Fraction(state.a.low) >= self.config.impulse_min_rise)
        qualified = price_ok and volume_ok
        self.state = replace(state, impulse_volumes=values,
                             impulse_volume_average=sum(map(Fraction, values), Fraction()) / len(values),
                             impulse_qualified=qualified,
                             lifecycle=State.PROVISIONAL_B if qualified else State.AB_IMPULSE)
        self._audit("IMPULSE_VOLUME_RECALCULATED", state.b_origin,
                    volumes="|".join(map(str, values)), average=str(self.state.impulse_volume_average),
                    impulse_qualified=qualified)
        if origin_index == 0:
            self._audit("A_ORIGIN_HIGH_NOT_SEQUENTIAL", state.a,
                        reason=ReasonCode.INTRABAR_SEQUENCE_AMBIGUITY)

    def _deadline(self, bar: MarketInterval) -> None:
        if (self.state.traded_candle_count >= self.config.impulse_max_traded_candles
                and not self.state.impulse_qualified):
            self._terminate(bar, "QUALIFYING_IMPULSE_ABSENT_AT_DEADLINE")

    def _advance(self, evidence: VolumeEvidence) -> None:
        bar, state = evidence.candle, self.state
        count = state.traded_candle_count + 1
        self.state = replace(state, impulse_candles=state.impulse_candles + (bar,))
        self._audit("AB_PROGRESS", bar, traded_candles=count)
        if count > self.config.impulse_max_traded_candles and bar.high > state.provisional_b_price:
            self._terminate(bar, "HIGH_ABOVE_PRICE_LOCKED_B")
            return
        if count <= self.config.impulse_max_traded_candles and bar.high >= state.provisional_b_price:
            equal = bar.high == state.provisional_b_price
            self.state = replace(self.state, provisional_b_price=bar.high, b_origin=bar,
                                 confirmation_candles=(), origin_retests=state.origin_retests +
                                 ((bar.timestamp,) if equal else ()))
            self._audit("B_EQUAL_HIGH_RETEST" if equal else "B_HIGHER_HIGH", bar,
                        previous_origin=state.b_origin.timestamp, b_price=bar.high,
                        confirmation_reset=True)
            self._recalculate()
        elif self.state.impulse_qualified:
            confirmations = self.state.confirmation_candles + (bar,)
            self.state = replace(self.state, confirmation_candles=confirmations)
            self._audit("B_CONFIRMATION_PROGRESS", bar, count=len(confirmations))
            if len(confirmations) == self.config.b_confirmation_traded_candles:
                if Fraction(bar.close) > Fraction(state.provisional_b_price) * (
                        1 - Fraction(self.config.b_confirmation_min_close_pullback)):
                    self._terminate(bar, "B_CONFIRMATION_PULLBACK_FAILED", SetupTermination.EXPIRED)
                    return
                self.state = replace(self.state, lifecycle=State.B_CONFIRMED,
                                     logical_b_confirmation_timestamp=bar.end,
                                     frozen_impulse_volume_average=self.state.impulse_volume_average)
                self._audit("B_CONFIRMED", bar, final_origin=state.b_origin.timestamp,
                            frozen_average=str(self.state.frozen_impulse_volume_average))
                return
        self._deadline(bar)
        if (self.state.lifecycle != State.TERMINATED and count >=
                self.config.impulse_max_traded_candles + self.config.b_max_post_impulse_confirmation_candles):
            self._terminate(bar, "B_CONFIRMATION_ALLOWANCE_EXHAUSTED", SetupTermination.EXPIRED)
