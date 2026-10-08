"""Immutable v1.0 defaults, transcribed from the canonical parameter table."""

from dataclasses import dataclass, fields
from datetime import time, timedelta
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class StrategyConfig:
    """Percent-named ADR/room values use percentage points; fractions use decimals.

    Research overrides create a separate instance. This is configuration only:
    no strategy computations or execution rules are implemented here.
    """

    starting_equity_usd: Decimal = Decimal("10000")
    risk_fraction_bod: Decimal = Decimal("0.01")
    max_shares_per_position: int = 1000
    max_new_position_equity_fraction: Decimal = Decimal("0.2")
    max_total_exposure_equity_fraction: Decimal = Decimal("0.6")
    max_simultaneous_positions: int = 3
    min_entry_shares: int = 2
    max_daily_entries: int = 5
    daily_net_realized_loss_fraction_bod: Decimal = Decimal("-0.02")
    max_consecutive_net_losing_trades: int = 3
    a_activation_min_rise: Decimal = Decimal("0.03")
    a_selection_max_traded_lookback: int = 10
    impulse_min_rise: Decimal = Decimal("0.08")
    impulse_min_traded_candles: int = 1
    impulse_max_traded_candles: int = 10
    impulse_participant_min_rvol: Decimal = Decimal("2.0")
    rvol_previous_intervals: int = 20
    b_confirmation_traded_candles: int = 2
    b_confirmation_min_close_pullback: Decimal = Decimal("0.01")
    b_max_post_impulse_confirmation_candles: int = 2
    c_min_retracement: Decimal = Decimal("0.2")
    c_max_retracement: Decimal = Decimal("0.5")
    c_max_development_traded_candles: int = 10
    c_min_consolidation_traded_candles: int = 3
    c_consolidation_range_lookback: int = 3
    c_max_consolidation_range_impulse_fraction: Decimal = Decimal("0.5")
    c_contraction_volume_lookback: int = 3
    c_required_subsequent_higher_low_candles: int = 1
    locked_c_max_first_attempt_elapsed_minutes: int = 10
    max_failed_breakout_attempts: int = 2
    breakout_resolution_elapsed_minutes: int = 5
    d_min_close_buffer_above_b: Decimal = Decimal("0.0025")
    d_max_close_extension_above_b: Decimal = Decimal("0.02")
    entry_max_open_extension_above_b: Decimal = Decimal("0.02")
    d_min_rvol: Decimal = Decimal("1.5")
    d_local_volume_previous_intervals: int = 3
    initial_stop_buffer_below_c: Decimal = Decimal("0.005")
    partial_target_r_multiple: Decimal = Decimal("2")
    partial_exit_fraction: Decimal = Decimal("0.5")
    runner_trailing_activation_traded_candles: int = 3
    runner_low_lookback_traded_candles: int = 3
    entry_cutoff_before_session_close: timedelta = timedelta(minutes=30)
    forced_liquidation_before_session_close: timedelta = timedelta(minutes=1)
    premarket_volume_history_start_et: time = time(4, 0)
    max_price_usd: Decimal = Decimal("5.00")
    min_current_day_change: Decimal = Decimal("0.03")
    min_market_cap_usd: Decimal = Decimal("50000000")
    adv_lookback_sessions: int = 10
    minimum_adv_shares: int = 1000000
    adr_lookback_sessions: int = 20
    high_min_adr_pct: Decimal = Decimal("5")
    very_high_min_adr_pct: Decimal = Decimal("10")
    context_timeframe_minutes: int = 15
    context_completed_blocks: int = 3
    context_comparison_lag_blocks: int = 2
    weekly_max_candidate_weeks: int = 52
    weekly_min_candidate_history_weeks: int = 12
    weekly_swing_left_bars: int = 2
    weekly_swing_right_bars: int = 2
    weekly_min_room_pct: Decimal = Decimal("5")
    commission_baseline_usd: Decimal = Decimal("0")
    explicit_fee_baseline_usd: Decimal = Decimal("0")
    slippage_baseline: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        # Validate representation, not unapproved research-parameter ranges.
        for field in fields(self):
            value = getattr(self, field.name)
            expected = field.type
            if type(value) is not expected:
                raise TypeError(f"{field.name} requires {expected.__name__}")
            if isinstance(value, Decimal) and not value.is_finite():
                raise ValueError(f"{field.name} must be finite")

    @property
    def max_total_breakout_attempts(self) -> int:
        """Derived limit: failed attempts plus one final attempt (#17)."""
        return self.max_failed_breakout_attempts + 1


FROZEN_V1 = StrategyConfig()
