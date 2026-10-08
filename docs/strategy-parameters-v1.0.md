# Strategy Spec v1.0 — Canonical Parameters

These are the frozen initial defaults for the [canonical specification](strategy-spec-v1.0.md), approved through Decisions #1–47. Percentages below are displayed as percentages; formulas use their decimal equivalents (1% = 0.01). Changing a research parameter creates a separately identified configuration, not a silent change to v1.0. Defaults are not proven optimal values.

## Numerical and timing defaults

| Parameter | Frozen default | Exact application / boundary | Decisions |
|---|---|---|---|
| `starting_equity_usd` | $10,000 | Initial simulated cash/equity, no borrowing | #7 |
| `risk_fraction_bod` | 1% | Maximum planned price risk; BOD equity remains fixed intraday | #7, #13 |
| `max_shares_per_position` | 1,000 | Inclusive quantity cap | #7 |
| `max_new_position_equity_fraction` | 20% | Current equity immediately before entry; whole shares floor | #7, #13 |
| `max_total_exposure_equity_fraction` | 60% | Proposed post-entry exposure must not exceed; already at/above cap prohibits new entry | #7, #13 |
| `max_simultaneous_positions` | 3 | Inclusive account cap | #7 |
| `min_entry_shares` | 2 | Below two rejects; whole quantities need not be even | #7 |
| `max_daily_entries` | 5 | Fifth accepted entry locks further entries | #10 |
| `daily_net_realized_loss_fraction_bod` | -2% | Lock when daily net realized P&L <= threshold | #10, #36 |
| `max_consecutive_net_losing_trades` | 3 | Third completed loss locks; WIN/BREAKEVEN resets | #10, #36 |
| `a_activation_min_rise` | 3% | Trigger HIGH rise from selected A >= threshold | #3, #24 |
| `a_selection_max_traded_lookback` | 10 | Up to available eligible fresh previous traded candles; trigger excluded | #24, #27, #29 |
| `impulse_min_rise` | 8% | A-to-B rise >= threshold | #2 |
| `impulse_min_traded_candles` | 1 | Approved duration lower bound; does not override #45's same-A-candle sequence prohibition | #2, #45 |
| `impulse_max_traded_candles` | 10 | A-origin candle counts as 1; no-trade intervals excluded | #2, #21, #29 |
| `impulse_participant_min_rvol` | 2.0x | At least one eligible participant's volume >= multiplier times its own positive baseline | #2, #24, #46 |
| `rvol_previous_intervals` | 20 | Exactly previous chronological eligible minutes; current excluded; same-day context only | #24, #27, #46 |
| `b_confirmation_traded_candles` | 2 | Consecutive actual traded candles after B origin; no-trade does not break sequence | #3, #29 |
| `b_confirmation_min_close_pullback` | 1% | Second confirmation close <= B × 0.99 | #3, #21 |
| `b_max_post_impulse_confirmation_candles` | 2 | Valid traded candles solely for confirmation; no extension of B price/impulse | #21, #29 |
| `c_min_retracement` | 20% | Inclusive fraction of A–B distance | #4, #5 |
| `c_max_retracement` | 50% | **Exclusive**, C strictly above midpoint | #5 |
| `c_max_development_traded_candles` | 10 | First traded candle after final B-origin = 1; includes B-confirmation candles | #22, #29 |
| `c_min_consolidation_traded_candles` | 3 | After current provisional C; no-trade excluded | #16, #29 |
| `c_consolidation_range_lookback` | 3 | Most recent qualifying actual traded consolidation candles | #4, #29 |
| `c_max_consolidation_range_impulse_fraction` | 50% | Three-candle max-high minus min-low <= 0.5 × (B−A) | #4 |
| `c_contraction_volume_lookback` | 3 | Mean actual consolidation volumes strictly below frozen impulse mean | #23 |
| `c_required_subsequent_higher_low_candles` | At least 1 | Subsequent traded low strictly above current provisional C | #4, #16 |
| `locked_c_max_first_attempt_elapsed_minutes` | 10 | First interval after locking = 1; traded and no-trade consume time | #19, #29 |
| `max_failed_breakout_attempts` | 2 | Third total attempt must qualify or expire | #17 |
| `max_total_breakout_attempts` | 3 | Derived initial attempt limit, not an independent contradictory setting | #17 |
| `breakout_resolution_elapsed_minutes` | 5 | First-attempt interval = 1; D may confirm at fifth close | #17, #29 |
| `d_min_close_buffer_above_b` | 0.25% | Close >= B × 1.0025 | #5 |
| `d_max_close_extension_above_b` | 2% | Close <= B × 1.02; greater expires | #5, #17 |
| `entry_max_open_extension_above_b` | 2% | Scheduled open <= B × 1.02 and strictly > B | #5 |
| `d_min_rvol` | 1.5x | Volume >= multiplier × positive previous-20 baseline | #5, #46 |
| `d_local_volume_previous_intervals` | 3 | Chronological regular-session minutes; volume comparison strictly > mean | #18, #30 |
| `initial_stop_buffer_below_c` | 0.5% | Raw stop C × 0.995, floor to applicable tick | #6, #34 |
| `partial_target_r_multiple` | 2R | Raw target from actual entry/rounded-stop risk; ceil to valid tick | #8, #34 |
| `partial_exit_fraction` | 50% | floor(original shares / 2); runner retains odd remainder | #8 |
| `runner_trailing_activation_traded_candles` | 3 | Additional completed traded candles after partial interval | #9, #32 |
| `runner_low_lookback_traded_candles` | 3 | Lowest of most recent three completed valid traded candles, prospective update | #9, #32 |
| `entry_cutoff_before_session_close` | 30 minutes | Actual entry time **strictly before** cutoff | #11 |
| `forced_liquidation_before_session_close` | 1 minute | Scheduled OPEN of final regular-session minute | #11 |
| `premarket_volume_history_start_et` | 04:00 | Same trading date, until regular open; volume context only | #27 |
| `max_price_usd` | $5.00 | Inclusive; close during active detection, scheduled open at entry | #37, #39 |
| `min_current_day_change` | +3% | Inclusive versus compatible prior official close | #37, #39, #43 |
| `min_market_cap_usd` | $50,000,000 | Inclusive, point-in-time | #37 |
| `adv_lookback_sessions` | 10 | Actual previous completed regular sessions; today excluded | #37 |
| `minimum_adv_shares` | 1,000,000 | **Strictly greater**, not >= | #37 |
| `adr_lookback_sessions` | 20 | Actual prior completed regular sessions, no skipping missing data | #38 |
| `high_min_adr_pct` | 5% | HIGH >=5 and <10; eligibility >=5 | #38 |
| `very_high_min_adr_pct` | 10% | VERY_HIGH >=10; no maximum ceiling | #38 |
| `context_timeframe_minutes` | 15 | Fixed current regular-session chronological blocks | #40, #41 |
| `context_completed_blocks` | 3 | Immediately preceding completed blocks, all valid traded | #40, #41 |
| `context_comparison_lag_blocks` | 2 | T3.high >= T1.high AND T3.low >= T1.low | #40 |
| `weekly_max_candidate_weeks` | 52 | Candidate origins only; current incomplete week excluded | #40, #42 |
| `weekly_min_candidate_history_weeks` | 12 | Minimum available completed applicable trading weeks | #40, #42 |
| `weekly_swing_left_bars` | 2 | Candidate strictly greater than both earlier highs | #40 |
| `weekly_swing_right_bars` | 2 | Candidate >= both following highs; both completed before entry | #40 |
| `weekly_min_room_pct` | 5% | Inclusive versus nearest confirmed level strictly above entry reference | #40 |
| `commission_baseline_usd` | $0 | Disclosed ZERO-FRICTION configuration, not verified broker schedule | #36, #44 |
| `explicit_fee_baseline_usd` | $0 | Same baseline restriction | #36, #44 |
| `slippage_baseline` | 0 | No hidden spread/impact adjustment; nonzero model separately approved | #36, #44 |

Runner lookbacks of 2/3/4/5, alternative wait windows, stop buffers and universe/volatility/volume parameters may be researched later; none is optimized or changed here. Minimum partial quantities, lag relations and derived limits must remain coherent in any separately approved configuration.

## Frozen policies, not unapproved tuning switches

| Policy | v1.0 value |
|---|---|
| Direction/instruments | Long only; supported U.S. common stocks; exclusions in specification |
| Timezone | America/New_York; actual official session calendar |
| Pattern sessions | Current regular session only; no overnight state |
| Minimum price / maximum ADR / maximum spread | **None approved**; do not invent numeric defaults |
| Exact tick size | Authoritative security/date/price/order-specific metadata; **no universal numeric default** |
| A equal-low tie | Earliest eligible occurrence |
| B equal-high tie | Latest eligible occurrence inside impulse window; no deadline extension |
| Price lookback versus volume context | Fresh traded price history; separately retained trustworthy chronological volume history |
| Zero previous-20 average | RVOL undefined; required qualification fails; no ranking sentinel |
| Local-three zero average | Literal strict absolute comparison; positive attempt may pass this test only |
| Setup overlap | One active setup per ticker; none while its position is open |
| Entry opportunity | Immediately next interval only; canceled/rejected setup consumed |
| Whole shares | floor; odd quantities allowed; no fractional shares |
| Margin / adding | None; no averaging down or adding to winners |
| Exposure constraints | Entry constraints, no automatic market-movement liquidation |
| Daily loss/streak basis | Net realized results; BOD threshold; locks sticky within session |
| Breakeven net classification | Exactly zero final net P&L resets streak |
| Initial stop / target normalization | Downward stop; upward target; precise tick arithmetic |
| Runner stop | Breakeven reference, downward normalization if needed; non-decreasing thereafter |
| Same-bar stop/target | Original stop first when sequence unknown; approved 2R/breakeven conservative exit |
| Setup ambiguity | Order-invariant result or adverse outcome; no invented high/low path |
| Fifteen-minute context | Mandatory; no skipping no-trade blocks or MA substitution |
| Weekly room | Mandatory; no resistance substitute if valid search finds none |
| Fill quantities | Full designated quantities; no depth/partial-fill inference from bar volume |
| OPEN event time | Interval start, explicit research abstraction |
| Same-time entries | D finite RVOL descending; alphabetical ticker tie-break |
| Opening event order | Known required exits/management first, then ranked entries |
| Later intrabar capital | Cannot alter earlier opening decisions |
| Fresh final-exit reset | Whole exit interval quarantined, including OPEN exits |
| Reporting marks | Carried last valid trade only for marked reporting, never fills/new-entry valuation |
| EOD no liquidity | No fabricated fill; shared run stops incomplete at official close |
| Missing held-position data | Shared pause; historical replacement replay; incomplete if unresolved |
| Tick failure | Pre-entry reject/consume; required open-position action failure terminates shared run |
| Corporate-action basis | Already-effective point-in-time share basis only; no double/future adjustment |
| Optimization/validation | Future separately approved research methodology, no claim of unbiased in-sample optimization |

Display precision, negligible numeric tolerance, timestamp labels, code precedence and exact fee-remainder bookkeeping must be documented deterministic implementation conventions. No numerical tolerance or arbitrary fee schedule has been invented in this table. Venue-specific active-order transformations, unusual open-position corporate actions, and nonzero-entry-slippage constraint behavior remain explicitly deferred models.
