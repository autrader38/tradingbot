# Frozen v1.0 implementation readiness

**Strategy Spec v1.0 is frozen. Decisions #1–47 are approved.** The full audit's four material rule blockers were resolved by #44–#47; no further strategy-design decisions are required before implementation.

Read the [canonical specification](strategy-spec-v1.0.md), [defaults](strategy-parameters-v1.0.md) and [decision history](strategy-decision-history.md). Old prototype thresholds, pivots, labels and moving-average filters are not authoritative. This readiness document adds no strategy rules.

## What is and is not ready

- Ready: frozen signal/risk/accounting rules, deterministic lifecycle and event-order requirements, explicit research execution assumptions, failure outcomes and audit contract.
- Unverified: availability, licensing, coverage and point-in-time reliability of a suitable historical dataset.
- Built in separately authorized Phase 1: typed configuration, minute/session models, validation, state/code enums, exact numerical primitives, audit records and offline foundation tests. See [Phase 1 guide](phase-1-foundation.md).
- Built in separately authorized Phase 2: per-ticker A activation, A/B impulse, provisional B/confirmation, chronological RVOL evidence and structured audits. See [Phase 2 guide](phase-2-ab-detector.md).
- Built in separately authorized Phase 3: a C extension with conditional post-B evidence, provisional-C updates, consolidation/higher-low qualification, C locking and C-stage termination. See [Phase 3 guide](phase-3-c-detector.md).
- Not built: breakout/D detector, complete transition engine, historical portfolio backtester, scanner, integration or execution service.
- Current authorization: foundation, A/B processing and C development/locking only. Do not implement later strategy phases or connect to external trading services until separately authorized.

**RESEARCH BACKTEST MODEL — V1.0**

**ZERO-FRICTION BASELINE**

**FULL-FILL RESEARCH ASSUMPTION — MARKET DEPTH AND PARTIAL FILLS NOT MODELED**

TRANSACTION COSTS AND SLIPPAGE NOT YET MODELED.

These labels must accompany applicable results. Specification readiness is not proof of profitability, realistic fills or paper/live deployment readiness.

## Required historical-data capability groups

| Required capability | Verification needed |
|---|---|
| Historical security master | Common-stock/depositary/instrument types, listing identity and dates, stable IDs, ticker reuse |
| Inactive/delisted coverage | Historically eligible failures as well as surviving names; disclose actual coverage limitations |
| Point-in-time market cap | Authoritative cap or applicable historical shares outstanding and compatible contemporaneous price |
| Prior official regular close | Trustworthy reference, adjusted only for already-effective applicable actions |
| Regular-session daily history | Actual prior ADV10/ADR20 sessions, volume/HLC and early-close handling |
| Completed weekly history | Up to 52 candidate weeks plus two older context weeks, minimum 12, confirmation and completeness |
| Corporate-action metadata | Effective dates/terms, source adjustment semantics, point-in-time normalization without double adjustment |
| Regular-session minute OHLCV | Eligible trades, trustworthy values/order/timestamps/corrections and security identifiers |
| Same-day premarket volume | 04:00 onward eligible chronological history, no previous-date substitution |
| Interval classification | Affirmative genuine no-trade evidence versus missing/invalid/corrupt/conflicting intervals |
| Historical ticks | Security/date/price/venue/order-specific valid increments and required replacement applicability |
| Official exchange calendar | Actual sessions, holidays, early closes, DST; unresolved boundaries flagged invalid |

Missing requirements use approved rejection/pause/incomplete outcomes; never guess data to obtain desired trades. Historical quotes are optional for initial spread reporting; depth, queues, routing, auction/next-day liquidation models and exact first-trade timestamps are not requirements of the approved baseline abstraction.

## Implementation checklist after separate authorization

- Use one canonical configuration from the frozen parameter table; record every departure as a separate research configuration.
- Specify data schemas for minute classifications, daily/weekly history, security references, corporate actions, ticks and calendar. Preserve raw/source-normalized/strategy-normalized provenance and availability.
- Implement separate ticker setup state and a single chronological shared account; preserve reset boundaries, traded/elapsed counters and finite RVOL ranking.
- Document timestamp labels alongside interval start/end and availability, deterministic primary-code precedence, negligible comparison tolerance and fee-remainder reconciliation. These are implementation conventions, not new strategy filters.
- Use precision-safe decimal/integer arithmetic for ticks and quantities; no invented numeric tolerances that alter approved equality rules.
- Implement exact data-pause, tick-failure and EOD-no-liquidity run outcomes; preserve completed results without fabricated continuation.
- Produce all canonical setup/entry/exit/portfolio/data-quality/termination audit records and explicit research disclosures.

## Meaningful validation fixtures

Use hand-checkable expected outcomes exercising the actual approved logic rather than mirroring implementation:

- A earliest-low and B latest-equal-high ties; permitted origin resets and post-deadline failures.
- Activation initialization with already-completed confirmation; no retrospective trades or same-A-candle impulse credit.
- Candle-10 impulse/C boundaries, two post-impulse confirmation slots, no-trade traded-count versus elapsed timers.
- Provisional-C lower-low resets, exact 20%/50% boundaries, locked-C touches/breaks and invalidation-versus-D ambiguity.
- Retry attempts, first-wait/resolution limits, D price/volume equalities and all-zero/mixed volume baselines.
- Chronological 15-minute aggregation, no-trade/invalid blocks, 10:15 context and completed weekly confirmation/window boundaries.
- Scheduled entry cancellation, dynamic gates, exact cutoff and early-close final-minute liquidation.
- Sizing caps, odd-share partials, simultaneous ranked entries and opening-exit cash versus later intrabar proceeds.
- Stop/target touches, gap prices, 2R/breakeven ambiguity, prospective non-decreasing trailing and no-trade runner periods.
- Tick normalization/unavailability, point-in-time split units and unavailable reference-data handling.
- Net cost allocation, net loss streaks/sticky lockouts, final-exit quarantine with retained volume context.
- Missing held data/replay, unresolved EOD no liquidity and required open-position tick failure; no false complete metrics.

Do not claim historical behavior verified until representative data-backed tests execute. A complete dataset may still expose incomplete runs; report them honestly. Parameter optimization/out-of-sample methodology, realistic commissions/fees/slippage/partial fills, automated paper acceptance and any live deployment remain future separately approved work.
