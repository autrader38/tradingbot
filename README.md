# AI Day Trader

A long-only ABCD Breakout research project. **Strategy Spec v1.0 is frozen:** approved Decisions #1–47 are permanently documented. A begins the impulse, B is its high/resistance, C is the controlled pullback, and D is the confirmed breakout above B.

## Read the frozen specification

- [Canonical Strategy Spec v1.0](docs/strategy-spec-v1.0.md): final authoritative rules, timing, state machine, accounting, data behavior and audit requirements.
- [Canonical parameter table](docs/strategy-parameters-v1.0.md): every frozen numerical default and policy boundary.
- [Approved decision history](docs/strategy-decision-history.md): Decisions #1–47 and supersession map.
- [Implementation readiness](docs/implementation-readiness.md): dataset requirements, remaining implementation work and validation checklist.
- [Architecture](docs/architecture.md): research components and separately gated future integration.
- [Phase 1 foundation](docs/phase-1-foundation.md): Python data/configuration primitives and offline test commands.
- [Phase 2 A/B detector](docs/phase-2-ab-detector.md): deterministic per-ticker activation, impulse and B confirmation with supplied historical inputs.
- [Phase 3 C development](docs/phase-3-c-detector.md): potential C, consolidation, higher lows and immutable C locking evidence.
- [Phase 4 D confirmation](docs/phase-4-d-detector.md): elapsed breakout timers, attempts, volume qualification and pending-entry signal handoff.
- [Phase 5 entry validation](docs/phase-5-entry-validation.md): opening-only checks, mandatory 15-minute/weekly context and approved candidates without fills.
- [Phase 6 trade construction](docs/phase-6-trade-construction.md): supplied ticks/account state, exact levels/sizing and simulated zero-friction opening entries.
- [Phase 7 position management](docs/phase-7-position-management.md): single-position stops/2R/runner/EOD exits, data pause/incomplete outputs and final-exit quarantine.

**RESEARCH BACKTEST MODEL — V1.0**

**ZERO-FRICTION BASELINE**

**FULL-FILL RESEARCH ASSUMPTION — MARKET DEPTH AND PARTIAL FILLS NOT MODELED**

Freezing the rules does not prove suitable historical data are available or establish realistic live execution. The initial research model uses explicit full-fill and opening-timestamp abstractions. Zero-friction results are not expected live returns. No profitability claim is made.

## Current repository

The repository contains frozen documentation, a dependency-free Python foundation, A/B/C/D detection, opening/context validation, simulated entry construction and single-position exit management with offline tests, a reference image, and a [historical chart-only Pine prototype](strategy/README.md). A completed historical backtester, scanner and portfolio/integration service remain unimplemented. The prototype's labels, parameters, pivots and moving-average filters do not implement frozen v1.0. Do not use it as the canonical strategy or connect its output to a broker.

The user separately authorized Phases 1–7: foundation, A/B/C/D detection, opening/context validation and single-position simulated entries/exits. This does not authorize a portfolio event loop, IBKR/TradingView connections or broker order placement. Later phases require separate authorization; paper and live execution require separately reviewed data, execution and integration readiness.

## Safety and next phases

Follow [AGENTS.md](AGENTS.md). Keep strategy rules deterministic and versioned; never commit credentials, account identifiers or real webhook endpoints. AI may explain results, but may not change active rules or risk limits.

After separate implementation authorization, prepare trustworthy point-in-time data and build/validate the deterministic historical research model. Later phases may evaluate realistic execution, observe paper results, scan live market data and consider reviewed automated paper execution. Live routing or a paper-to-live switch requires later explicit approval.

TradingView/IBKR with a hosted bridge was an earlier integration candidate, not a selected or connected service. [Bridge evaluation notes](docs/bridge-evaluation.md) are retained for future verification, not part of the historical fill model. A signal is not evidence of a broker fill.
