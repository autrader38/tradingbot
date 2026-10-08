"""A/B extension for potential C, consolidation and C locking only."""

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from fractions import Fraction

from .ab_detector import ABDetector, EligibilityInputs
from .audit import AuditRecord
from .codes import ReasonCode, SetupTermination
from .config import FROZEN_V1, StrategyConfig
from .market import IntervalClassification, MarketInterval
from .sessions import SessionCalendar
from .states import StrategyState as State
from .volume import VolumeEvidence


@dataclass(frozen=True, slots=True)
class ConsolidationMetrics:
    price_range: Fraction
    average_volume: Fraction
    range_passed: bool
    volume_passed: bool


def consolidation_metrics(candles: tuple[MarketInterval, ...],
                          volume_candles: tuple[MarketInterval, ...], impulse: Fraction,
                          impulse_average: Fraction, max_range_fraction: Fraction) -> ConsolidationMetrics:
    """Exact numerical comparisons; price eligibility is checked by the tracker."""
    if not candles or not volume_candles or any(
            bar.classification != IntervalClassification.TRADED for bar in candles + volume_candles):
        raise ValueError("Supply actual traded consolidation candles")
    price_range = Fraction(max(bar.high for bar in candles)) - Fraction(min(bar.low for bar in candles))
    average = sum((Fraction(bar.volume) for bar in volume_candles), Fraction()) / len(volume_candles)
    return ConsolidationMetrics(price_range, average,
                                price_range <= max_range_fraction * impulse,
                                average < impulse_average)


@dataclass(frozen=True, slots=True)
class CLockEvidence:
    c_origin: MarketInterval
    retracement: Fraction
    development_candles: tuple[MarketInterval, ...]
    higher_low_candles: tuple[MarketInterval, ...]
    consolidation_candles: tuple[MarketInterval, ...]
    volume_candles: tuple[MarketInterval, ...]
    metrics: ConsolidationMetrics
    frozen_impulse_average: Fraction


@dataclass(frozen=True, slots=True)
class CState:
    b_origin: MarketInterval | None = None
    development_candles: tuple[MarketInterval, ...] = ()
    lowest_pullback: MarketInterval | None = None
    provisional_origin: MarketInterval | None = None
    retracement: Fraction | None = None
    higher_low_candles: tuple[MarketInterval, ...] = ()
    consolidation_candles: tuple[MarketInterval, ...] = ()
    potential_failure: str | None = None
    locked_price: Decimal | None = None
    locked_origin: MarketInterval | None = None
    lock_timestamp: datetime | None = None
    lock_evidence: CLockEvidence | None = None

    @property
    def development_count(self) -> int:
        return len(self.development_candles)


class CDetector(ABDetector):
    """Combined detector. ABDetector remains available for A/B-only consumers.

    Potential C data is conditional on the exact B origin. No C state is
    official until B confirmation is recognized. Locking occurs at information
    availability, never retrospectively during initial A/B history replay.
    """

    def __init__(self, security_id: str, ticker: str, calendar: SessionCalendar,
                 *, run_id: str, data_version: str, config: StrategyConfig = FROZEN_V1):
        super().__init__(security_id, ticker, calendar, run_id=run_id,
                         data_version=data_version, config=config)
        self.c_state = CState()
        self._initializing = False

    def feed(self, bar: MarketInterval, inputs: EligibilityInputs) -> tuple[AuditRecord, ...]:
        events = super().feed(bar, inputs)
        if self.state.setup_id is None:
            self.c_state = CState()
        return events

    def _recalculate(self) -> None:
        old = self.c_state.b_origin
        super()._recalculate()
        self.c_state = CState(b_origin=self.state.b_origin)
        self._audit("POTENTIAL_C_RESTARTED" if old else "POTENTIAL_C_STARTED",
                    self.state.b_origin, previous_b_origin=old.timestamp if old else None,
                    eligible_after=self.state.b_origin.end,
                    discarded_development=True if old else False)

    def _terminate(self, bar: MarketInterval, description: str,
                   termination: SetupTermination = SetupTermination.INVALIDATED,
                   reason: ReasonCode | None = None) -> None:
        super()._terminate(bar, description, termination, reason)
        if self.state.logical_b_confirmation_timestamp is None and self.c_state.b_origin is not None:
            self._audit("POTENTIAL_C_DISCARDED", bar, description="B did not successfully confirm")
            self.c_state = CState()

    def _activate(self, participants: list[VolumeEvidence], qualifying: tuple[VolumeEvidence, ...],
                  rise: Fraction) -> None:
        self.c_state = CState()
        self._initializing = True
        try:
            super()._activate(participants, qualifying, rise)
            if self.state.lifecycle == State.B_CONFIRMED:
                # A/B replay stops when B confirms. C must also examine all
                # remaining eligible known candles before its first official
                # lock, rather than pretend it locked in the past.
                processed = (self.c_state.development_candles[-1].timestamp
                             if self.c_state.development_candles else self.state.b_origin.timestamp)
                for item in participants:
                    if item.candle.timestamp > processed:
                        self._consume(item.candle)
        finally:
            self._initializing = False
        if self.state.lifecycle == State.B_CONFIRMED:
            self._finish(self._current)

    def _advance(self, evidence: VolumeEvidence) -> None:
        previous_origin = self.state.b_origin.timestamp
        super()._advance(evidence)
        if self.state.lifecycle == State.TERMINATED:
            return
        if self.state.b_origin.timestamp == previous_origin:
            self._consume(evidence.candle)
        if self.state.lifecycle == State.B_CONFIRMED and not self._initializing:
            self._finish(evidence.candle)

    def _process_active(self, evidence: VolumeEvidence) -> None:
        if self.state.lifecycle == State.C_LOCKED:
            self._check_locked(evidence.candle)
        elif self.state.logical_b_confirmation_timestamp is not None:
            self._consume(evidence.candle)
            self._finish(evidence.candle)
        else:
            super()._process_active(evidence)

    def _consume(self, bar: MarketInterval) -> None:
        c = self.c_state
        if c.b_origin is None or bar.timestamp <= c.b_origin.timestamp:
            raise ValueError("C evidence must be strictly after the current B origin")
        if c.development_candles and bar.timestamp <= c.development_candles[-1].timestamp:
            raise ValueError("C evidence cannot be counted twice or reordered")
        self.c_state = replace(c, development_candles=c.development_candles + (bar,))
        self._audit("C_DEVELOPMENT_PROGRESS", bar, count=self.c_state.development_count,
                    potential=self.state.logical_b_confirmation_timestamp is None)
        if c.potential_failure is not None:
            return
        # B is fixed for the surviving post-origin sequence. Before B confirms,
        # legal B moves restart tracking rather than reach this branch.
        if bar.high > self.state.provisional_b_price:
            self.c_state = replace(self.c_state, potential_failure="EARLY_BREAK_ABOVE_B")
            self._audit("C_INVALIDATING_EVIDENCE", bar, description="EARLY_BREAK_ABOVE_B")
            return
        impulse = Fraction(self.state.provisional_b_price) - Fraction(self.state.a.low)
        retracement = (Fraction(self.state.provisional_b_price) - Fraction(bar.low)) / impulse
        if (retracement >= self.config.c_max_retracement
                or Fraction(bar.low) <= (Fraction(self.state.a.low) + Fraction(self.state.provisional_b_price)) / 2
                or bar.low <= self.state.a.low):
            self.c_state = replace(self.c_state, lowest_pullback=bar,
                                   potential_failure="C_RETRACEMENT_AT_OR_BELOW_MIDPOINT")
            self._audit("C_INVALIDATING_EVIDENCE", bar,
                        description="C_RETRACEMENT_AT_OR_BELOW_MIDPOINT", retracement=str(retracement))
            return
        if c.lowest_pullback is None or bar.low < c.lowest_pullback.low:
            self.c_state = replace(self.c_state, lowest_pullback=bar)
        if c.provisional_origin is None and retracement < self.config.c_min_retracement:
            self._audit("C_RETRACEMENT_NOT_YET_QUALIFIED", bar, retracement=str(retracement))
            return
        if c.provisional_origin is None or bar.low < c.provisional_origin.low:
            self.c_state = replace(self.c_state, provisional_origin=bar, retracement=retracement,
                                   higher_low_candles=(), consolidation_candles=())
            potential = self.state.logical_b_confirmation_timestamp is None
            event = ("POTENTIAL_C_UPDATED" if c.provisional_origin else "POTENTIAL_C_ESTABLISHED") if potential else (
                "PROVISIONAL_C_UPDATED" if c.provisional_origin else "PROVISIONAL_C_ESTABLISHED")
            self._audit(event,
                        bar, c_price=bar.low, retracement=str(retracement),
                        potential=self.state.logical_b_confirmation_timestamp is None,
                        previous_c=c.provisional_origin.low if c.provisional_origin else None,
                        higher_low_reset=True, consolidation_reset=True,
                        development_count=self.c_state.development_count)
            return
        self.c_state = replace(self.c_state, consolidation_candles=c.consolidation_candles + (bar,))
        if bar.low > c.provisional_origin.low:
            self.c_state = replace(self.c_state, higher_low_candles=c.higher_low_candles + (bar,))
            self._audit("C_HIGHER_LOW", bar, low=bar.low, c_price=c.provisional_origin.low)
        self._audit("C_CONSOLIDATION_PROGRESS", bar,
                    count=len(self.c_state.consolidation_candles),
                    higher_low_count=len(self.c_state.higher_low_candles))

    def _finish(self, bar: MarketInterval) -> None:
        if self.state.lifecycle == State.TERMINATED:
            return
        c = self.c_state
        if c.potential_failure is not None:
            self._terminate(bar, c.potential_failure)
            return
        if self.state.lifecycle == State.B_CONFIRMED:
            self.state = replace(self.state, lifecycle=State.C_DEVELOPING)
            self._audit("C_DEVELOPING", bar, b_confirmation=self.state.logical_b_confirmation_timestamp,
                        existing_development_count=c.development_count)
        if c.provisional_origin is not None:
            required = max(self.config.c_min_consolidation_traded_candles,
                           self.config.c_consolidation_range_lookback,
                           self.config.c_contraction_volume_lookback)
            if len(c.consolidation_candles) >= required:
                window = c.consolidation_candles[-self.config.c_consolidation_range_lookback:]
                volumes = c.consolidation_candles[-self.config.c_contraction_volume_lookback:]
                # The frozen v1.0 lookbacks are both three. Keep calculations
                # separate so a labeled research config need not alter defaults.
                metrics = consolidation_metrics(
                    window, volumes, Fraction(self.state.provisional_b_price) - Fraction(self.state.a.low),
                    self.state.frozen_impulse_volume_average,
                    Fraction(self.config.c_max_consolidation_range_impulse_fraction))
                higher_lows = tuple(item for item in window if item.low > c.provisional_origin.low)
                higher_passed = len(higher_lows) >= self.config.c_required_subsequent_higher_low_candles
                self._audit("C_CONSOLIDATION_EVALUATED", bar,
                            intervals="|".join(item.timestamp.isoformat() for item in window),
                            volume_intervals="|".join(item.timestamp.isoformat() for item in volumes),
                            price_range=str(metrics.price_range), average_volume=str(metrics.average_volume),
                            frozen_impulse_average=str(self.state.frozen_impulse_volume_average),
                            range_passed=metrics.range_passed, volume_passed=metrics.volume_passed,
                            higher_low_passed=higher_passed)
                if (metrics.range_passed and metrics.volume_passed and higher_passed
                        and c.development_count <= self.config.c_max_development_traded_candles):
                    self.c_state = replace(c, locked_price=c.provisional_origin.low,
                                           locked_origin=c.provisional_origin,
                                           lock_timestamp=self._current.end,
                                           lock_evidence=CLockEvidence(
                                               c.provisional_origin, c.retracement,
                                               c.development_candles, higher_lows, window, volumes,
                                               metrics, self.state.frozen_impulse_volume_average))
                    self.state = replace(self.state, lifecycle=State.C_LOCKED)
                    self._audit("C_LOCKED", bar, c_price=c.provisional_origin.low,
                                c_origin=c.provisional_origin.timestamp,
                                lock_timestamp=self.c_state.lock_timestamp,
                                development_count=c.development_count)
                    return
        if c.development_count >= self.config.c_max_development_traded_candles:
            self._terminate(bar, "C_DEVELOPMENT_WINDOW_EXHAUSTED", SetupTermination.EXPIRED)

    def _check_locked(self, bar: MarketInterval) -> None:
        if bar.low < self.c_state.locked_price:
            reason = None
            if bar.high > self.state.provisional_b_price:
                reason = ReasonCode.C_INVALIDATION_INTRABAR_AMBIGUITY
                self._audit("C_LOCKED_INVALIDATION_AMBIGUITY", bar,
                            reason=ReasonCode.INTRABAR_SEQUENCE_AMBIGUITY)
            self._terminate(bar, "PRICE_BELOW_LOCKED_C", reason=reason)
        else:
            self._audit("LOCKED_C_PRESERVED", bar, c_price=self.c_state.locked_price)
